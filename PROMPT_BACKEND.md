> **DOCUMENT HISTORIQUE — PÉRIMÉ, NE PAS APPLIQUER TEL QUEL.**
>
> Ce fichier est la spécification d'origine, conservée pour la traçabilité. Depuis, la
> réalité technique a divergé sur plusieurs points (TimescaleDB indisponible, codes de
> rejet unifiés, états et colonnes manquants, stop et take-profit redéfinis).
>
> Les sources qui font foi sont, dans cet ordre :
> `backend/db/migrations/*.sql` → `backend/app/engine/risk_gate.py` →
> `docs/DECISIONS.md` → `docs/ROADMAP.md`.
>
> Pour comprendre l'écart entre ce document et le code : `docs/DECISIONS.md` (D-001 à
> D-008) et l'entrée du 2026-11-04 dans `docs/JOURNAL.md`.

---

Spécification Technique & Architecture : Backend Automate Trading IACe document constitue le brief technique de référence pour l'implémentation du moteur d'exécution et de décision. Le backend suit une architecture événementielle découplée, garantissant que le LLM n'exécute aucune action transactionnelle de manière discrétionnaire sans validation par un moteur de règles déterministe (Risk Gateway).1. Principes d'Architecture & Ségrégation des ResponsabilitésLe système est découpé en cinq couches isolées communicant via des interfaces typées :[ Exchange WS / REST ] ──> [ Market Data Engine ] ──> [ Feature Pipeline (TA-Lib) ]
                                                              │
                                                              ▼
[ Exchange Execution ] <── [ Deterministic Risk Gate ] <── [ LLM Decision Tier ]
         │                          ▲
         ▼                          │
[ PostgreSQL Ledger ]      [ Admin / Kill-Switch API ]
Market Data & Feature Engine : Ingestion temps réel (WebSocket) des ticks/bougies, calcul des indicateurs techniques vectorisés, détection de régime de marché.LLM Decision Tier : Construction d'un prompt d'état synthétique, inférence locale via endpoint compatible OpenAI (Ollama/vLLM), extraction d'un payload JSON strictement validé.Deterministic Risk Gateway (Garde-fous absolus) : Validation logique mathématique (aucun ordre ne passe sans franchir tous les filtres), dimensionnement de position, calcul du Stop-Loss/Take-Profit matériel.Execution Layer : Abstraction exchange unifiée via CCXT (ordres Stop-Market distants sur l'exchange).State & Observability Layer : Persistance PostgreSQL, télémétrie WebSocket vers le frontend, notifications d'urgence.2. Compatibilité Multi-OS (Windows, Linux, Docker)Pour assurer une portabilité native sans friction entre Windows (développement local), Linux (WSL2 / serveur) et Docker :Réseau & Host d'Inférence : L'URL de l'API LLM doit être injectée via variable d'environnement (LLM_BASE_URL).Linux / WSL2 : [http://127.0.0.1:11434/v1](http://127.0.0.1:11434/v1)Docker (accès à Ollama sur l'hôte) : [http://host.docker.internal:11434/v1](http://host.docker.internal:11434/v1)Gestion de l'Event Loop asyncio : uvloop n'est pas supporté nativement sous Windows. L'initialisation doit être conditionnée :Pythonimport sys
import asyncio

if sys.platform != "win32":
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
Système de fichiers : Utilisation stricte du module pathlib.Path pour bannir les séparateurs de chemins codés en dur (/ vs \).Dépendances système (TA-Lib) : Préférer pandas-ta (pur Python / Numba) à TA-Lib C-wrapper pour éliminer le besoin de compilation C++ sous Windows nativement, ou encapsuler le runtime complet dans un conteneur multi-stage Linux.3. Structure Modulaire du ProjetPlaintextbackend/
├── app/
│   ├── api/                     # Endpoints FastAPI (REST & WebSockets)
│   │   ├── deps.py              # Dépendances (Auth JWT, RBAC, DB session)
│   │   └── v1/
│   │       ├── admin.py         # Start/Stop, Kill-Switch, Reconfiguration
│   │       ├── auth.py          # Login, TOTP 2FA, Refresh Tokens
│   │       ├── monitoring.py    # Métriques système, logs d'audit, PnL
│   │       └── websocket.py     # Flux temps réel pour le Dashboard
│   ├── core/                    # Configuration, secrets et logging
│   │   ├── config.py            # Pydantic Settings (chargement .env)
│   │   ├── logging.py           # Structlog JSON formaté
│   │   └── security.py          # Chiffrement AES des clés, hash Argon2, JWT
│   ├── domain/                  # Entités et schémas stricts
│   │   ├── models/              # SQLAlchemy / SQLModel ORM (Trades, Orders, Logs)
│   │   └── schemas/             # Schémas Pydantic (Signaux, Inférence LLM, Risque)
│   ├── engine/                  # Cœur métier du bot
│   │   ├── data_feed.py         # CCXT Pro WebSocket stream manager
│   │   ├── features.py          # Calcul des indicateurs (EMA, RSI, ATR)
│   │   ├── llm_client.py        # Client d'inférence structurée (Instructor / Outlines)
│   │   ├── risk_gate.py         # Moteur de règles déterministe (Garde-fous)
│   │   └── executor.py          # Routage et réconciliation des ordres CCXT
│   └── main.py                  # Point d'entrée de l'application
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
├── alembic/                     # Migrations de schéma BDD
├── requirements.txt
└── .env.example
4. Spécification des Garde-Fous (Risk Gateway Déterministe)Le module risk_gate.py agit comme un proxy bloquant. Toute proposition du LLM est invalidée si l'une des assertions suivantes échoue :Garde-FouImplémentation Mathématique / RègleAction en cas de non-respectTendance FondamentaleInterdiction d'achat si $\text{Price} \le \text{EMA}_{200}$ sur timeframe 4h.Signal LLM ignoré. Statut : REJECTED_COUNTER_TREND.Anti-Falling KnifeRefus d'achat si la bougie 1h précédente affiche un drawdown $> 2.5 \times \text{ATR}_{14}$ sans clôture haussière de confirmation.Signal LLM ignoré. Statut : REJECTED_EXTREME_VOLATILITY.Stop-Loss MatérielCalcul dynamique : $\text{SL} = \text{Entry} - (1.5 \times \text{ATR}_{14})$. Ordre stop-market obligatoirement posé sur l'exchange à la milliseconde suivant l'achat.Annulation de la transaction si l'ordre SL distant est rejeté.Max Position SizingRisque fixé à maximum 1% de l'équité totale par transaction : $\text{Size} = \frac{\text{Capital} \times 0.01}{\text{Entry} - \text{SL}}$.Réajustement automatique de la taille ou annulation si inférieure au minimum exchange.Profit Sweeper (Réserve)À la clôture d'un trade avec gain net : 30% du delta de profit est immédiatement transféré vers le portefeuille Cold/Reserve (ou converti en stablecoin non-alloué au bot).Transaction comptable automatique post-trade.Circuit BreakerDrawdown journalier $\ge 3\%$ du capital OU 3 échecs consécutifs.Verrouillage du bot (BOT_LOCKED), notification d'urgence, intervention manuelle 2FA requise.5. Protocole d'Inférence LLM & Validation StructuréeLe modèle (ex. Qwen 2.5 14B) reçoit un résumé factuel sans bruit et doit obligatoirement répondre sous un schéma JSON strict.Schéma Pydantic imposé à l'inférencePythonfrom pydantic import BaseModel, Field
from typing import Literal

class LLMTradingDecision(BaseModel):
    action: Literal["BUY", "SELL", "HOLD"]
    confidence: float = Field(..., ge=0.0, le=1.0, description="Niveau de certitude du modèle")
    market_regime: Literal["TRENDING_UP", "TRENDING_DOWN", "RANGING", "VOLATILE"]
    thesis: str = Field(..., max_length=200, description="Explication concise en 1-2 phrases")
Prompt Système & Contexte InjectéRôle : Analyste quantitatif de risque. Son rôle n'est pas de spéculer, mais d'identifier des confluences statistiques.Payload injecté :JSON{
  "symbol": "BTC/USDT",
  "timestamp_utc": "2026-10-30T06:00:00Z",
  "market_data": {
    "price": 68450.0,
    "ema_50": 68100.0,
    "ema_200": 67200.0,
    "rsi_14": 52.3,
    "atr_14": 420.0,
    "orderbook_imbalance_bid_ratio": 0.58
  },
  "portfolio": {
    "usdt_free": 12500.0,
    "current_position": null
  }
}
Gestion des anomalies : Timeout d'inférence fixé à 5 secondes. En cas de timeout, d'erreur de parsing ou d'invalidité du JSON, le système bascule automatiquement sur l'action HOLD sans planter le worker.6. Sécurité & Authentification AdminLe backend expose une interface d'administration sécurisée pour le dashboard :Authentification Dual-Factor :Étape 1 : Signature JWT avec algorithme EdDSA (Ed25519) ou RS256, expiration 15 minutes, stocké dans un cookie HttpOnly, Secure, SameSite=Strict.Étape 2 : Validation TOTP (RFC 6238 / Google Authenticator via pyotp) requise pour toute action critique (/api/v1/admin/bot/start, /stop, /panic-sell).Protection des Clés API Exchange :Les clés API et Secrets de l'exchange ne sont jamais exposés au frontend.Stockage chiffré au repos via AES-256-GCM avec une clé de déchiffrement maîtresse injectée via variable d'environnement (MASTER_ENCRYPTION_KEY).Vérification au démarrage : le backend interroge les capacités de la clé via CCXT. Si l'autorisation "Withdrawal" est active, le backend refuse de démarrer avec une erreur critique.7. Configuration Déployable (Docker & Environnement).env.exampleExtrait de code# Environnement
ENV=production
DEBUG=false
TZ=UTC

# Base de Données
DATABASE_URL=postgresql+asyncpg://trading_user:trading_secure_pass@localhost:5432/trading_db

# LLM Local (Ollama ou vLLM)
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL_NAME=qwen2.5:14b-instruct-q4_K_M

# Exchange Config (Testnet par défaut)
EXCHANGE_ID=binance
EXCHANGE_API_KEY=your_testnet_api_key
EXCHANGE_SECRET=your_testnet_secret
EXCHANGE_SANDBOX_MODE=true

# Sécurité & Admin
JWT_SECRET_KEY=generate_with_openssl_rand_hex_32
TOTP_SECRET=generate_base32_secret
MASTER_ENCRYPTION_KEY=generate_with_openssl_rand_hex_32
CORS_ORIGINS=["http://localhost:3000"]
docker/DockerfileDockerfileFROM python:3.12-slim-bookworm AS builder

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.12-slim-bookworm AS runner
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PATH=/root/.local/bin:$PATH

COPY --from=builder /root/.local /root/.local
COPY . .

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
docker/docker-compose.ymlYAMLservices:
  postgres:
    image: timescale/timescaledb:latest-pg16
    container_name: trading_postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: trading_user
      POSTGRES_PASSWORD: trading_secure_pass
      POSTGRES_DB: trading_db
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U trading_user -d trading_db"]
      interval: 5s
      timeout: 5s
      retries: 5

  backend:
    build:
      context: ..
      dockerfile: docker/Dockerfile
    container_name: trading_backend
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
    env_file:
      - ../.env
    environment:
      DATABASE_URL: postgresql+asyncpg://trading_user:trading_secure_pass@postgres:5432/trading_db
    ports:
      - "8000:8000"
    extra_hosts:
      - "host.docker.internal:host-gateway"

volumes:
  pgdata:
