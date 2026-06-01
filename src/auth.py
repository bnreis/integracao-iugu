"""
Autenticação JWT para a API de gestão (app mobile).

Fluxo:
  1. POST /auth/login com {usuario, senha}
  2. Se válido, retorna um token JWT
  3. Todas as rotas protegidas exigem o header Authorization: Bearer <token>

Configuração no .env:
  API_USUARIO=admin
  API_SENHA=sua_senha_forte
  API_JWT_SECRET=chave_secreta_aleatoria
  API_JWT_EXPIRA_HORAS=72
"""
from __future__ import annotations

import secrets
import time as _time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from loguru import logger
from pydantic import BaseModel

from .config import settings

# Schema de login
class LoginRequest(BaseModel):
    usuario: str
    senha: str

class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # segundos

class UsuarioAutenticado(BaseModel):
    usuario: str
    exp: datetime


# Chave JWT — usa a configurada ou gera uma aleatória (reinício invalida tokens)
_JWT_SECRET = settings.api_jwt_secret or secrets.token_hex(32)
_JWT_ALGORITHM = "HS256"

# Security scheme do FastAPI (extrai Bearer token do header)
_security = HTTPBearer(auto_error=False)


def _verificar_credenciais(usuario: str, senha: str) -> bool:
    """Verifica se usuário e senha batem com o configurado no .env.

    Usa secrets.compare_digest (comparação em tempo constante) para evitar
    timing attacks na descoberta de usuário/senha.
    """
    if not settings.api_senha:
        logger.warning(
            "API_SENHA não configurada no .env — login desabilitado por segurança"
        )
        return False
    usuario_ok = secrets.compare_digest(usuario, settings.api_usuario)
    senha_ok = secrets.compare_digest(senha, settings.api_senha)
    return usuario_ok and senha_ok


# --- Rate limiting do login (in-memory, sliding window por IP) ---
# Single-worker uvicorn → dict em memória é suficiente. Reinicia o contador
# a cada restart do serviço (aceitável para uso single-operador).
_LOGIN_MAX_TENTATIVAS = 5
_LOGIN_JANELA_SEG = 60
_login_tentativas: dict[str, deque] = defaultdict(deque)


def client_ip(request: Request) -> str:
    """IP real do cliente. Atrás do Apache, usa X-Forwarded-For (1º IP da cadeia)."""
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _verificar_rate_limit_login(ip: str) -> None:
    """Bloqueia (429) após _LOGIN_MAX_TENTATIVAS dentro da janela. Registra a tentativa."""
    agora = _time.time()
    tentativas = _login_tentativas[ip]
    while tentativas and agora - tentativas[0] > _LOGIN_JANELA_SEG:
        tentativas.popleft()
    if len(tentativas) >= _LOGIN_MAX_TENTATIVAS:
        logger.warning(f"Rate limit de login atingido para ip={ip}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitas tentativas de login. Aguarde 1 minuto e tente novamente.",
        )
    tentativas.append(agora)


def _resetar_rate_limit_login(ip: str) -> None:
    """Zera o contador após login bem-sucedido."""
    _login_tentativas.pop(ip, None)


def gerar_token(usuario: str) -> LoginResponse:
    """Gera um token JWT para o usuário autenticado."""
    expira_em = datetime.now(timezone.utc) + timedelta(hours=settings.api_jwt_expira_horas)
    payload = {
        "sub": usuario,
        "exp": expira_em,
        "iat": datetime.now(timezone.utc),
    }
    token = jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)
    return LoginResponse(
        access_token=token,
        expires_in=settings.api_jwt_expira_horas * 3600,
    )


def login(request: LoginRequest, ip: str = "?") -> LoginResponse:
    """Autentica o usuário e retorna um token JWT (com rate limiting por IP)."""
    _verificar_rate_limit_login(ip)
    if not _verificar_credenciais(request.usuario, request.senha):
        logger.warning(f"Tentativa de login falhou: usuario={request.usuario} ip={ip}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário ou senha inválidos",
        )
    _resetar_rate_limit_login(ip)
    logger.info(f"Login bem-sucedido: {request.usuario} ip={ip}")
    return gerar_token(request.usuario)


def _decodificar_token(token: str) -> dict:
    """Decodifica e valida um token JWT."""
    try:
        payload = jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expirado — faça login novamente",
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token inválido: {e}",
        )


async def usuario_autenticado(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_security),
) -> UsuarioAutenticado:
    """
    Dependency do FastAPI — injeta o usuário autenticado nas rotas protegidas.

    Uso:
        @router.get("/rota-protegida")
        async def minha_rota(user: UsuarioAutenticado = Depends(usuario_autenticado)):
            ...
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de autenticação não fornecido",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = _decodificar_token(credentials.credentials)
    return UsuarioAutenticado(
        usuario=payload["sub"],
        exp=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
    )
