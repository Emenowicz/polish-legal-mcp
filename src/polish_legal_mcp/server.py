"""FastMCP server: tool definitions (Polish descriptions), auth, rate limiting, health check."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, Literal
from urllib.parse import urlsplit

import fastmcp
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AuthContext
from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.auth.providers.google import GoogleProvider
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import AuthMiddleware, Middleware
from fastmcp.server.middleware.middleware import MiddlewareContext
from fastmcp.server.middleware.rate_limiting import RateLimitingMiddleware
from key_value.aio.stores.filetree import FileTreeStore
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.middleware import Middleware as StarletteMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from polish_legal_mcp import __version__
from polish_legal_mcp.clients.eli import EliClient
from polish_legal_mcp.clients.saos import SaosClient
from polish_legal_mcp.config import Settings
from polish_legal_mcp.errors import LegalMcpError
from polish_legal_mcp.models import (
    ActId,
    AmendmentsResult,
    CitationVerificationResult,
    JudgmentDetails,
    JudgmentSearchResult,
    LegalActDetails,
    LegalBasisResult,
    ProvisionResult,
    SearchActsResult,
)
from polish_legal_mcp.security import RequestGuard, build_oauth_storage, periodic_cull
from polish_legal_mcp.services.judgments import JudgmentService
from polish_legal_mcp.services.legal import LegalService

logger = logging.getLogger(__name__)

INSTRUCTIONS = """\
Serwer udostępnia oficjalne źródła prawa polskiego: akty prawne z API ELI Kancelarii Sejmu
(Dziennik Ustaw, Monitor Polski) oraz orzeczenia z SAOS. Zasady:
- Cytuj wyłącznie treść zwróconą przez narzędzia wraz z linkiem do źródła.
- Statusy weryfikacji są cząstkowe (VERIFIED, UNVERIFIED, NOT_FOUND, NOT_APPLICABLE,
  SOURCE_UNAVAILABLE). Twierdzenia, które nie mają statusu VERIFIED, oznaczaj wyraźnie jako
  niezweryfikowane. Nie uzupełniaj brakującej treści z pamięci.
- Brak wyniku nie dowodzi, że przepis lub orzeczenie nie istnieje.
- Od 2025 r. ELI publikuje teksty tylko w PDF – brzmienie może pochodzić ze starszego tekstu
  jednolitego; zawsze sprawdź ostrzeżenia i listę późniejszych zmian.
- Orzeczenia nie są przepisami prawa. SAOS nie obejmuje sądów administracyjnych.
- Wynik nie jest opinią prawną.
"""

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True, idempotent_hint=True)

ArticleStr = Annotated[
    str,
    Field(
        min_length=1,
        max_length=20,
        pattern=r"(?i)^\s*(art\.?\s*|§\s*)?\d+[a-z]{0,3}\s*$",
        description="Numer artykułu, np. '11' lub '4a'. Dla rozporządzeń dzielonych na paragrafy: '§ 5'.",
    ),
]
ParagraphStr = Annotated[
    str | None,
    Field(
        default=None,
        max_length=20,
        pattern=r"(?i)^\s*(ust\.?\s*|§\s*)?\d+[a-z]{0,3}\s*$",
        description="Ustęp lub paragraf w artykule, np. '1', 'ust. 2' albo '§ 1' (kodeksy).",
    ),
]
PointStr = Annotated[
    str | None,
    Field(
        default=None,
        max_length=20,
        pattern=r"(?i)^\s*(pkt\.?\s*)?\d+[a-z]{0,3}\s*$",
        description="Punkt, np. '2'.",
    ),
]
LetterStr = Annotated[
    str | None,
    Field(
        default=None,
        max_length=10,
        pattern=r"(?i)^\s*(lit\.?\s*)?[a-z]{1,2}\s*$",
        description="Litera, np. 'a'.",
    ),
]
ActIdStr = Annotated[
    str,
    Field(
        min_length=5,
        max_length=60,
        description="Identyfikator aktu: ELI 'DU/1960/168' albo adres 'Dz.U. 1960 nr 30 poz. 168'.",
    ),
]
AsOfDate = Annotated[
    date | None,
    Field(default=None, description="Data stanu prawnego (RRRR-MM-DD). Domyślnie dzisiaj."),
]


async def _run[T](call: Callable[[], Awaitable[T]]) -> T:
    try:
        return await call()
    except LegalMcpError as exc:
        raise ToolError(str(exc)) from exc


def allowlist_check(settings: Settings) -> Callable[[AuthContext], bool]:
    """Allow GitHub logins (case-insensitive) or Google e-mails verified by Google.

    Denied identities are logged once per process: a signed-in account that is not on the
    allowlist otherwise just sees an empty tool list, which is hard to diagnose.
    """
    denied_logged: set[str] = set()

    def deny(identity: str) -> bool:
        if identity not in denied_logged:
            denied_logged.add(identity)
            logger.warning(
                "signed-in account not on allowlist; tools hidden",
                extra={"fields": {"identity": identity}},
            )
        return False

    if settings.auth_mode == "google":
        emails = {email.lower() for email in settings.allowed_emails}

        def check_google(ctx: AuthContext) -> bool:
            claims = ctx.token.claims if ctx.token else {}
            email, verified = claims.get("email"), claims.get("email_verified")
            if not isinstance(email, str):
                return deny("google:<no e-mail claim>")
            if str(verified).lower() != "true":
                return deny(f"google:{email} (e-mail not verified by Google)")
            return email.lower() in emails or deny(f"google:{email}")

        return check_google
    logins = {login.lower() for login in settings.allowed_github_logins}

    def check_github(ctx: AuthContext) -> bool:
        login = ctx.token.claims.get("login") if ctx.token else None
        if not isinstance(login, str):
            return deny("github:<no login claim>")
        return login.lower() in logins or deny(f"github:{login}")

    return check_github


def _rate_limit_key(context: MiddlewareContext) -> str:
    token = get_access_token()
    if token is None:
        return "anonymous"
    user = token.claims.get("login") or token.claims.get("email")
    return f"user:{user}" if isinstance(user, str) else f"client:{token.client_id}"


def build_auth(
    settings: Settings,
) -> tuple[GitHubProvider | GoogleProvider | None, FileTreeStore | None]:
    """OAuth provider plus the raw OAuth store (to cull), or (None, None) without auth."""
    if settings.auth_mode == "none":
        return None, None
    assert settings.base_url is not None
    jwt_key = settings.jwt_signing_key.get_secret_value() if settings.jwt_signing_key else None
    storage, store = (
        build_oauth_storage(fastmcp.settings.home, jwt_key) if jwt_key else (None, None)
    )
    if settings.auth_mode == "google":
        assert settings.google_client_secret is not None
        provider: GitHubProvider | GoogleProvider = GoogleProvider(
            client_id=str(settings.google_client_id),
            client_secret=settings.google_client_secret.get_secret_value(),
            base_url=settings.base_url,
            # Least privilege: only the verified e-mail is needed for the allowlist.
            required_scopes=["openid", "email"],
            jwt_signing_key=jwt_key,
            client_storage=storage,
            # Claude and Claude Code identify themselves with a Client ID Metadata Document
            # (no registration stored on disk); DCR stays available for other MCP clients.
            enable_cimd=True,
        )
    else:
        assert settings.github_client_secret is not None
        provider = GitHubProvider(
            client_id=str(settings.github_client_id),
            client_secret=settings.github_client_secret.get_secret_value(),
            base_url=settings.base_url,
            # Least privilege: only the GitHub login is needed for the allowlist.
            required_scopes=["read:user"],
            jwt_signing_key=jwt_key,
            client_storage=storage,
            # Claude and Claude Code identify themselves with a Client ID Metadata Document
            # (no registration stored on disk); DCR stays available for other MCP clients.
            enable_cimd=True,
        )
    return provider, store


def build_server(settings: Settings, *, backoff_base: float = 0.5) -> FastMCP:
    eli = EliClient(settings, backoff_base=backoff_base)
    saos = SaosClient(settings, backoff_base=backoff_base)
    legal = LegalService(eli)
    judgments = JudgmentService(saos)

    @asynccontextmanager
    async def lifespan(_: FastMCP) -> AsyncIterator[None]:
        try:
            async with periodic_cull(oauth_store):
                yield
        finally:
            await eli.aclose()
            await saos.aclose()

    middleware: list[Middleware] = [
        RateLimitingMiddleware(
            max_requests_per_second=settings.rate_limit_per_second,
            burst_capacity=settings.rate_limit_burst,
            get_client_id=_rate_limit_key,
        )
    ]
    auth, oauth_store = build_auth(settings)
    if auth is not None:
        middleware.insert(0, AuthMiddleware(auth=allowlist_check(settings)))

    mcp = FastMCP(
        name="polish-legal-mcp",
        instructions=INSTRUCTIONS,
        version=__version__,
        auth=auth,
        middleware=middleware,
        lifespan=lifespan,
        mask_error_details=True,
    )

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "version": __version__})

    @mcp.tool(annotations=READ_ONLY)
    async def search_legal_acts(
        query: Annotated[
            str,
            Field(
                min_length=2,
                max_length=300,
                description="Fragment(y) tytułu aktu. Tytuły są odmienione – używaj rdzeni, "
                "np. 'gospodarce nieruchomo', 'zakwaterowa'.",
            ),
        ],
        publisher: Annotated[
            Literal["DU", "MP"] | None,
            Field(default=None, description="DU – Dziennik Ustaw, MP – Monitor Polski."),
        ] = None,
        year: Annotated[
            int | None, Field(default=None, ge=1918, le=2100, description="Rok publikacji.")
        ] = None,
        limit: Annotated[int, Field(ge=1, le=50, description="Maksymalna liczba wyników.")] = 10,
        act_type: Annotated[
            str | None,
            Field(
                default=None,
                max_length=40,
                description="Typ aktu wg ELI, np. 'Ustawa', 'Rozporządzenie'.",
            ),
        ] = None,
        in_force_only: Annotated[
            bool, Field(description="Tylko akty oznaczone w ELI jako obowiązujące.")
        ] = False,
        keyword: Annotated[
            str | None,
            Field(
                default=None,
                max_length=100,
                description="Hasło ze słownika ELI, np. 'nieruchomości'.",
            ),
        ] = None,
    ) -> SearchActsResult:
        """Wyszukuje akty prawne (ustawy, rozporządzenia, obwieszczenia) w oficjalnym API ELI
        Sejmu RP po fragmentach tytułu i opcjonalnie haśle przedmiotowym. Zwraca identyfikatory
        ELI, adresy publikacji (Dz.U./M.P.), status i linki do ISAP. Nie jest to wyszukiwanie
        pełnotekstowe ani semantyczne. Akty podstawowe są pokazywane przed zmieniającymi."""
        return await _run(
            lambda: legal.search_acts(
                query, publisher, year, limit, act_type, in_force_only, keyword
            )
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_legal_act(
        publisher: Annotated[
            Literal["DU", "MP"], Field(description="DU – Dziennik Ustaw, MP – Monitor Polski.")
        ],
        year: Annotated[int, Field(ge=1918, le=2100, description="Rok publikacji.")],
        position: Annotated[int, Field(ge=1, le=99999, description="Pozycja w dzienniku.")],
        toc_offset: Annotated[int, Field(ge=0, description="Przesunięcie spisu jednostek.")] = 0,
        toc_limit: Annotated[int, Field(ge=1, le=500, description="Liczba pozycji spisu.")] = 200,
    ) -> LegalActDetails:
        """Pobiera metadane aktu prawnego z ELI: tytuł, status, daty (ogłoszenia, wejścia w życie,
        uchylenia), dostępne formaty tekstu (HTML/PDF) z linkami, powiązania (zmiany, teksty
        jednolite) oraz stronicowany spis rozdziałów i artykułów. Nie zwraca całej treści –
        brzmienie przepisu pobierz narzędziem get_legal_provision."""
        return await _run(
            lambda: legal.get_act(ActId(publisher, year, position), toc_offset, toc_limit)
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_legal_provision(
        act_id: ActIdStr,
        article: ArticleStr,
        paragraph: ParagraphStr = None,
        point: PointStr = None,
        letter: LetterStr = None,
        as_of_date: AsOfDate = None,
    ) -> ProvisionResult:
        """Pobiera dosłowne brzmienie przepisu (art./ust./§/pkt/lit.) z oficjalnego tekstu HTML w
        ELI, wybierając najnowszy tekst jednolity dostępny na żądaną datę. Zwraca ścieżkę
        strukturalną, przypisy, gotowy cytat, link do źródła, listę zmian po stanie tekstu oraz
        cząstkowe statusy weryfikacji. Jeśli obowiązywania brzmienia na datę nie da się ustalić,
        status temporal_applicability to UNVERIFIED – wtedy wyraźnie zaznacz to użytkownikowi."""
        return await _run(
            lambda: legal.get_provision(
                ActId.parse(act_id), article, paragraph, point, letter, as_of_date
            )
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_legal_act_amendments(
        act_id: ActIdStr,
        as_of_date: AsOfDate = None,
        limit: Annotated[int, Field(ge=1, le=100, description="Liczba najnowszych zmian.")] = 20,
    ) -> AmendmentsResult:
        """Zwraca metadane zmian aktu z ELI: akty zmieniające z datami, zmiany oczekujące na wejście
        w życie po wskazanej dacie, zmiany po najnowszym tekście jednolitym oraz orzeczenia TK.
        Lista pochodzi z metadanych ELI i nie musi być kompletna."""
        return await _run(lambda: legal.get_amendments(ActId.parse(act_id), as_of_date, limit))

    @mcp.tool(annotations=READ_ONLY)
    async def verify_legal_citation(
        citation: Annotated[
            str,
            Field(
                min_length=3,
                max_length=1000,
                description="Cytat, np. 'art. 61 § 1 ustawy z dnia 14 czerwca 1960 r. Kodeks "
                "postępowania administracyjnego (Dz. U. z 2025 r. poz. 1691)' lub 'art. 5 k.c.'.",
            ),
        ],
        as_of_date: AsOfDate = None,
    ) -> CitationVerificationResult:
        """Weryfikuje cytat podstawy prawnej: rozbiera go, ustala akt, sprawdza istnienie aktu i
        jednostki redakcyjnej, pobiera brzmienie i analizuje zmiany. Osobno raportuje: istnienie
        aktu, istnienie jednostki, dosłowne brzmienie, obowiązywanie na datę i kompletność zmian.
        Nie ocenia, czy przepis jest właściwy merytorycznie dla sprawy."""
        return await _run(lambda: legal.verify_citation(citation, as_of_date))

    @mcp.tool(annotations=READ_ONLY)
    async def find_legal_basis(
        issue_description: Annotated[
            str,
            Field(
                min_length=3,
                max_length=2000,
                description="Opis zagadnienia, bez danych osobowych i poufnych.",
            ),
        ],
        as_of_date: AsOfDate = None,
        limit: Annotated[int, Field(ge=1, le=30, description="Liczba kandydatów.")] = 10,
    ) -> LegalBasisResult:
        """Wskazuje akty prawne – kandydatów na podstawę prawną – dopasowując słowa opisu do haseł
        przedmiotowych ELI i tytułów aktów obowiązujących. To dopasowanie leksykalne, nie
        semantyczne i nie porada prawna: każdego kandydata zweryfikuj get_legal_act /
        get_legal_provision."""
        return await _run(lambda: legal.find_legal_basis(issue_description, as_of_date, limit))

    @mcp.tool(annotations=READ_ONLY)
    async def search_judgments(
        query: Annotated[
            str,
            Field(
                min_length=2, max_length=300, description="Fraza do wyszukania w treści orzeczeń."
            ),
        ],
        court_type: Annotated[
            Literal["COMMON", "SUPREME", "CONSTITUTIONAL_TRIBUNAL", "NATIONAL_APPEAL_CHAMBER"]
            | None,
            Field(default=None, description="Rodzaj sądu: powszechny, Sąd Najwyższy, TK, KIO."),
        ] = None,
        date_from: Annotated[
            date | None, Field(default=None, description="Orzeczenia od dnia.")
        ] = None,
        date_to: Annotated[
            date | None, Field(default=None, description="Orzeczenia do dnia.")
        ] = None,
        limit: Annotated[int, Field(ge=1, le=50, description="Wyników na stronę.")] = 10,
        page: Annotated[int, Field(ge=0, le=1000, description="Numer strony (od 0).")] = 0,
        referenced_regulation: Annotated[
            str | None,
            Field(
                default=None,
                max_length=200,
                description="Fragment tytułu aktu powołanego w orzeczeniu.",
            ),
        ] = None,
    ) -> JudgmentSearchResult:
        """Wyszukuje orzeczenia sądów w SAOS (sądy powszechne, SN, TK, KIO) – zwraca sygnatury,
        sąd, datę, fragment i link. SAOS nie zawiera wszystkich orzeczeń, w szczególności
        orzeczeń sądów administracyjnych (WSA/NSA). Orzeczenie nie jest przepisem prawa."""
        return await _run(
            lambda: judgments.search(
                query, court_type, date_from, date_to, limit, page, referenced_regulation
            )
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_judgment(
        judgment_id: Annotated[int, Field(ge=1, description="Identyfikator orzeczenia w SAOS.")],
        text_offset: Annotated[int, Field(ge=0, description="Przesunięcie w treści (znaki).")] = 0,
        text_limit: Annotated[
            int, Field(ge=500, le=20000, description="Długość fragmentu treści.")
        ] = 8000,
        search_text: Annotated[
            str | None,
            Field(
                default=None,
                max_length=200,
                description="Fraza – zwraca fragmenty treści, w których występuje.",
            ),
        ] = None,
    ) -> JudgmentDetails:
        """Pobiera orzeczenie z SAOS: sygnatury, sąd, datę, rodzaj, sędziów, podstawy prawne,
        powołane przepisy oraz stronicowaną treść (sentencja/uzasadnienie) i opcjonalnie fragmenty
        z szukaną frazą. Zawiera link do strony orzeczenia w SAOS."""
        return await _run(lambda: judgments.get(judgment_id, text_offset, text_limit, search_text))

    return mcp


def build_app(settings: Settings):
    """ASGI app with Streamable HTTP at /mcp (stateless, so any request can be served alone)."""
    mcp = build_server(settings)
    allowed_hosts = list(settings.allowed_hosts)
    allowed_origins: list[str] = []
    if settings.base_url:
        url = urlsplit(settings.base_url)
        if url.hostname:
            allowed_hosts.append(url.hostname)
            # TLS ends at the proxy (Funnel/Caddy), so the app sees http while browsers send the
            # public https Origin (e.g. the OAuth consent form); same-origin matching would fail.
            allowed_origins.append(f"{url.scheme}://{url.netloc}")
    return mcp.http_app(
        path="/mcp",
        middleware=[StarletteMiddleware(RequestGuard)],
        stateless_http=True,
        host_origin_protection=True if allowed_hosts else "auto",
        allowed_hosts=allowed_hosts or None,
        allowed_origins=allowed_origins or None,
    )
