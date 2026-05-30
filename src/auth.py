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
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
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
    """Verifica se usuário e senha batem com o configurado no .env."""
    if not settings.api_senha:
        logger.warning(
            "API_SENHA não configurada no .env — login desabilitado por segurança"
        )
        return False
    return usuario == settings.api_usuario and senha == settings.api_senha


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


def login(request: LoginRequest) -> LoginResponse:
    """Autentica o usuário e retorna um token JWT."""
    if not _verificar_credenciais(request.usuario, request.senha):
        logger.warning(f"Tentativa de login falhou: usuario={request.usuario}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário ou senha inválidos",
        )
    logger.info(f"Login bem-sucedido: {request.usuario}")
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
