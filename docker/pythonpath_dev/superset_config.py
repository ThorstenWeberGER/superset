# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
#
# This file is included in the final Docker image and SHOULD be overridden when
# deploying the image to prod. Settings configured here are intended for use in local
# development environments. Also note that superset_config_docker.py is imported
# as a final step as a means to override "defaults" configured here
#
import logging
import os
import sys

from celery.schedules import crontab
from flask_caching.backends.filesystemcache import FileSystemCache

from superset.tasks.types import ExecutorType

logger = logging.getLogger()

DATABASE_DIALECT = os.getenv("DATABASE_DIALECT")
DATABASE_USER = os.getenv("DATABASE_USER")
DATABASE_PASSWORD = os.getenv("DATABASE_PASSWORD")
DATABASE_HOST = os.getenv("DATABASE_HOST")
DATABASE_PORT = os.getenv("DATABASE_PORT")
DATABASE_DB = os.getenv("DATABASE_DB")

EXAMPLES_USER = os.getenv("EXAMPLES_USER")
EXAMPLES_PASSWORD = os.getenv("EXAMPLES_PASSWORD")
EXAMPLES_HOST = os.getenv("EXAMPLES_HOST")
EXAMPLES_PORT = os.getenv("EXAMPLES_PORT")
EXAMPLES_DB = os.getenv("EXAMPLES_DB")

# The SQLAlchemy connection string.
SQLALCHEMY_DATABASE_URI = (
    f"{DATABASE_DIALECT}://"
    f"{DATABASE_USER}:{DATABASE_PASSWORD}@"
    f"{DATABASE_HOST}:{DATABASE_PORT}/{DATABASE_DB}"
)

# Use environment variable if set, otherwise construct from components
# This MUST take precedence over any other configuration
SQLALCHEMY_EXAMPLES_URI = os.getenv(
    "SUPERSET__SQLALCHEMY_EXAMPLES_URI",
    (
        f"{DATABASE_DIALECT}://"
        f"{EXAMPLES_USER}:{EXAMPLES_PASSWORD}@"
        f"{EXAMPLES_HOST}:{EXAMPLES_PORT}/{EXAMPLES_DB}"
    ),
)


REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = os.getenv("REDIS_PORT", "6379")
REDIS_CELERY_DB = os.getenv("REDIS_CELERY_DB", "0")
REDIS_RESULTS_DB = os.getenv("REDIS_RESULTS_DB", "1")

RESULTS_BACKEND = FileSystemCache("/app/superset_home/sqllab")

CACHE_CONFIG = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_DEFAULT_TIMEOUT": 300,
    "CACHE_KEY_PREFIX": "superset_",
    "CACHE_REDIS_HOST": REDIS_HOST,
    "CACHE_REDIS_PORT": REDIS_PORT,
    "CACHE_REDIS_DB": REDIS_RESULTS_DB,
}

# Chart/dataset query results - kept separate from CACHE_CONFIG above so
# this TTL can be long (a Snowflake-outage safety net) without also making
# the general/session cache stale for two days. A chart only re-queries
# Snowflake once its cached entry expires; the cache-warmup job below
# refreshes every entry well inside this window, and a failed query never
# overwrites a good cache entry (superset/common/query_context_processor.py
# only writes to cache after a successful query), so a warm-up that hits
# Snowflake mid-outage just fails silently and the old entry stays valid.
DATA_CACHE_CONFIG = {
    **CACHE_CONFIG,
    "CACHE_DEFAULT_TIMEOUT": 60 * 60 * 48,  # 48h outage safety net
    "CACHE_KEY_PREFIX": "superset_data_",
}

# Cache warm-up (below) re-queries Snowflake as this executor. Default is
# each chart's Owner, which is fine as long as no chart's result depends
# on who's viewing it (no Row-Level Security in use yet). If per-viewer RLS
# ships later (RBAC doc chapter 8, Partner_Row_Isolation), warming as a
# single Owner only refreshes that owner's RLS variant - every other viewer
# would still miss cache during an outage. Revisit this list then.
CACHE_WARMUP_EXECUTORS = [ExecutorType.OWNER]

THUMBNAIL_CACHE_CONFIG = CACHE_CONFIG


class CeleryConfig:
    broker_url = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_CELERY_DB}"
    imports = (
        "superset.sql_lab",
        "superset.tasks.scheduler",
        "superset.tasks.thumbnails",
        "superset.tasks.cache",
    )
    result_backend = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_RESULTS_DB}"
    worker_prefetch_multiplier = 1
    task_acks_late = False
    beat_schedule = {
        "reports.scheduler": {
            "task": "reports.scheduler",
            "schedule": crontab(minute="*", hour="*"),
        },
        "reports.prune_log": {
            "task": "reports.prune_log",
            "schedule": crontab(minute=10, hour=0),
        },
        # Re-queries every chart every 3h, well inside DATA_CACHE_CONFIG's
        # 48h TTL above, so a Snowflake outage is bridged by cache instead
        # of erroring for users.
        "cache-warmup-every-3-hours": {
            "task": "cache-warmup",
            "schedule": crontab(minute=0, hour="*/3"),
            "kwargs": {"strategy_name": "dummy"},
        },
    }


CELERY_CONFIG = CeleryConfig

FEATURE_FLAGS = {
    "ALERT_REPORTS": True,
    "DATASET_FOLDERS": True,
    # Lets a dashboard's own Roles field gate visibility, on top of dataset
    # access. Required for the C-Level / partner dashboard isolation below.
    "DASHBOARD_RBAC": True,
}
ALERT_REPORTS_NOTIFICATION_DRY_RUN = True
WEBDRIVER_BASEURL = f"http://superset_app{os.environ.get('SUPERSET_APP_ROOT', '/')}/"  # When using docker compose baseurl should be http://superset_nginx{ENV{BASEPATH}}/  # noqa: E501
# The base URL for the email report hyperlinks.
WEBDRIVER_BASEURL_USER_FRIENDLY = (
    f"http://localhost:8888/{os.environ.get('SUPERSET_APP_ROOT', '/')}/"
)
SQLLAB_CTAS_NO_LIMIT = True

log_level_text = os.getenv("SUPERSET_LOG_LEVEL", "INFO")
LOG_LEVEL = getattr(logging, log_level_text.upper(), logging.INFO)

if os.getenv("CYPRESS_CONFIG") == "true":
    # When running the service as a cypress backend, we need to import the config
    # located @ tests/integration_tests/superset_test_config.py
    base_dir = os.path.dirname(__file__)
    module_folder = os.path.abspath(
        os.path.join(base_dir, "../../tests/integration_tests/")
    )
    sys.path.insert(0, module_folder)
    from superset_test_config import *  # noqa

    sys.path.pop(0)

#
# ---------------------------------------------------------------------------
# Microsoft Entra (Azure AD) single sign-on
# ---------------------------------------------------------------------------
# Secrets are read from the environment, set in the App Deploy env box and
# routed into the container by the extra `.env` entry added to the Superset
# services in docker-compose.yml. Nothing sensitive is committed here.
# Required env vars: AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET
#
from flask_appbuilder.security.manager import AUTH_OAUTH  # noqa: E402

AUTH_TYPE = AUTH_OAUTH

# A user who logs in but matches no app role lands on Public, which sees
# nothing. Real access comes only from the group -> Gamma mapping below.
AUTH_USER_REGISTRATION = True
AUTH_USER_REGISTRATION_ROLE = "Public"

# Re-evaluate role membership on every login, so removing someone from the
# Entra group revokes their Superset access at their next login.
AUTH_ROLES_SYNC_AT_LOGIN = True

# Superset sits behind the deploy tool's TLS reverse proxy; trust the
# forwarded headers so the OAuth redirect URI is built as https, not http.
ENABLE_PROXY_FIX = True

AZURE_TENANT_ID = os.environ.get("AZURE_TENANT_ID")

OAUTH_PROVIDERS = [
    {
        "name": "azure",
        "icon": "fa-windows",
        "token_key": "access_token",
        "remote_app": {
            "client_id": os.environ.get("AZURE_CLIENT_ID"),
            "client_secret": os.environ.get("AZURE_CLIENT_SECRET"),
            "api_base_url": f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/oauth2",
            "request_token_url": None,
            "access_token_url": f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/oauth2/token",
            "authorize_url": f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/oauth2/authorize",
            "jwks_uri": "https://login.microsoftonline.com/common/discovery/v2.0/keys",
            "client_kwargs": {"scope": "openid email profile"},
        },
    }
]

# Entra App Role value  ->  Superset role(s).
# Assign each Entra security group to the matching App Role under
# Entra ID > App registrations > Superset > App roles, then assign that
# App Role to the group under Enterprise Applications > Superset > Users
# and groups. Entra then emits every assigned App Role value in the
# token's `roles` claim, and a user in more than one assigned group gets
# the union of every matching list below (AUTH_ROLES_SYNC_AT_LOGIN above
# replaces their role list with that union on every login).
#
# "Admin" is the original app role, already assigned in Entra - left as-is
# so existing logins keep working. The plain "Gamma" app role (no scope
# role attached) has been dropped: it granted zero implicit data access,
# so every group that matters is now covered explicitly below instead.
#
# Everything below is the access-matrix rollout: it depends on three
# custom Superset roles (Scope_Gold_Full, Scope_Gold_Restricted,
# Creator_Capability) that must exist in Settings > List Roles first,
# with permissions set per the RBAC doc chapter 2.2, or the matching part
# of a user's role list is silently dropped (unknown role names are
# ignored, not an error).
#
# External Partners are intentionally not here: they're generally not in
# the Bloomwell Entra tenant, and are handled via Superset Groups instead
# (RBAC doc chapter 11), not this AD-driven mapping.
AUTH_ROLES_MAPPING = {
    "Admin": ["Admin"],
    "C_Level": ["Gamma", "Scope_Gold_Full"],
    "General_Management": ["Gamma", "Scope_Gold_Restricted"],
    "Finance_Employees": ["Gamma", "Scope_Gold_Full"],
    "Employees": ["Gamma", "Scope_Gold_Restricted"],
    # Admin alone already implies unrestricted access to every database
    # and schema - Alpha/Public would stack on top without adding
    # anything functionally (RBAC doc 2.1).
    "Data_Team": ["Admin"],
    "Superset_Creators": ["sql_lab", "Creator_Capability"],
}

#
# MCP service (superset/mcp_service) - internal, VPN-only, dev-mode auth
# ---------------------------------------------------------------------------
# superset/mcp_service/mcp_config.py has NO default for MCP_DEV_USERNAME and
# raises "No authenticated user found" without it. It must be a real Flask
# config value - setting it only as a container `environment:` var in
# docker-compose.yml's superset-mcp service does nothing on its own, since
# Superset doesn't auto-map arbitrary OS env vars into Flask config.
#
# Every MCP call acts as this one fixed user (create it via the UI with a
# restricted role - Scope_Gold_Full + Creator_Capability, not Admin - see
# docker-compose.yml's superset-mcp service). Fine for a handful of trusted,
# VPN-only callers; not a substitute for per-caller auth if this ever needs
# to be reachable more broadly (see superset/mcp_service/PRODUCTION.md's
# MCP_AUTH_ENABLED / JWT setup for that case - Entra could double as the
# issuer since it's already wired up above).
MCP_DEV_USERNAME = os.environ.get("MCP_DEV_USERNAME")

#
# ---------------------------------------------------------------------------
# Bloomwell branding — app theme, fonts, and chart colors
# ---------------------------------------------------------------------------
# Two surfaces, both needed: the app UI (THEME_DEFAULT / THEME_DARK) and the
# colors charts are actually drawn in (EXTRA_*_COLOR_SCHEMES). Setting the app
# color alone does NOT change chart series colors — the palette does that.
#

APP_NAME = "Bloomwell Analytics"
# APP_ICON = "https://bloomwellit.blob.core.windows.net/frimen-logos/<logo-file>"     # TODO: logo
# FAVICONS = [{"href": "https://bloomwellit.blob.core.windows.net/frimen-logos/<favicon-file>"}]  # TODO

# Gellix is hosted in Azure Blob Storage, so that host must be allowlisted here.
# This list also feeds the font-src / style-src CSP, so the browser is allowed
# to load it. (Azure Blob also needs anonymous read + a CORS rule — see manual.)
THEME_FONT_URL_ALLOWED_DOMAINS = [
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "use.typekit.net",
    "use.typekit.com",
    "bloomwellit.blob.core.windows.net",
]

# Shared brand tokens. Only colors and fonts are overridden; the rest stays on
# Superset's defaults.
_BLOOMWELL_TOKEN = {
    "brandAppName": APP_NAME,
    "brandLogoAlt": "Bloomwell",
    "brandLogoMargin": "18px 0",
    "brandLogoHref": "/",
    "brandLogoHeight": "24px",
    "brandSpinnerUrl": None,
    "brandSpinnerSvg": None,
    "brandIconMaxWidth": 37,
    # Colors — Bloomwell palette
    "colorPrimary": "#2A9D8F",   # brand teal: buttons, links, active states
    "colorLink": "#2A9D8F",
    "colorInfo": "#2A9D8F",
    "colorSuccess": "#5AC189",   # kept distinct so success != primary
    "colorError": "#AB041A",     # secondary dark — on-brand error red
    "colorWarning": "#FCC700",
    "colorEditorSelection": "#DDF3F1",  # pale teal SQL Lab highlight
    # Fonts — Gellix via the hosted @font-face stylesheet
    "fontFamily": "Gellix, Inter, Helvetica, Arial, sans-serif",
    "fontFamilyCode": "'IBM Plex Mono', 'Courier New', monospace",
    "fontUrls": ["https://bloomwellit.blob.core.windows.net/frimen-logos/gellix.css"],
    # Weights mapped to the Gellix files that exist (no 300, so light = 400)
    "fontWeightLight": "400",
    "fontWeightNormal": "400",
    "fontWeightStrong": "500",
    "fontWeightBold": "700",
    "transitionTiming": 0.3,
}

# Light look (default)
THEME_DEFAULT = {
    "token": _BLOOMWELL_TOKEN,
    "algorithm": "default",
}

# Dark look — same brand tokens, dark algorithm, with a lighter link/highlight
# for contrast on dark backgrounds. Defining both enables the user light/dark
# toggle and OS-preference detection.
THEME_DARK = {
    "token": {
        **_BLOOMWELL_TOKEN,
        "colorLink": "#7DC4BC",
        "colorEditorSelection": "#1D877A",
    },
    "algorithm": "dark",
}

# Admins can still preview/tune themes in the UI on top of these.
ENABLE_UI_THEME_ADMINISTRATION = True

# Chart series colors (categorical). isDefault makes new charts use it.
EXTRA_CATEGORICAL_COLOR_SCHEMES = [
    {
        "id": "bloomwell",
        "label": "Bloomwell",
        "description": "Bloomwell brand palette",
        "isDefault": True,
        "colors": [
            "#2A9D8F",  # primary teal
            "#FF5E73",  # secondary coral
            "#272D2D",  # brand dark
            "#7DC4BC",  # tertiary teal
            "#AB041A",  # secondary dark
            "#94CEC7",  # primary 50
            "#FFAEB9",  # secondary 50
            "#1D877A",  # primary dark
            "#CAE7E3",  # primary 25
            "#FFD7DC",  # secondary 25
        ],
    }
]

# Gradients / heatmaps (sequential): single-hue teal ramp, light to dark.
EXTRA_SEQUENTIAL_COLOR_SCHEMES = [
    {
        "id": "bloomwellTeal",
        "label": "Bloomwell Teal",
        "isDefault": True,
        "colors": [
            "#EAF5F4", "#DDF3F1", "#CAE7E3", "#94CEC7",
            "#7DC4BC", "#2A9D8F", "#1D877A",
        ],
    }
]

#
# Optionally import superset_config_docker.py (which will have been included on
# the PYTHONPATH) in order to allow for local settings to be overridden
#
try:
    import superset_config_docker
    from superset_config_docker import *  # noqa: F403

    logger.info(
        "Loaded your Docker configuration at [%s]", superset_config_docker.__file__
    )
except ImportError:
    logger.info("Using default Docker config...")
