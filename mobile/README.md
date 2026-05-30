# App Android — Iugu NFS-e (MEGASUPORTE)

App para monitorar e gerenciar faturas, NFS-e e empresas do sistema de integração Iugu.

## Funcionalidades

- **Dashboard**: resumo do dia (faturas criadas/pagas, NFS-e emitidas, erros)
- **Faturas**: listar, filtrar, ver detalhes, cancelar, gerar NFS-e, reenviar e-mail
- **Empresas**: listar, ver detalhes, ativar/desativar, controlar emissão de NF-e

## Setup do Backend (API)

### 1. Instalar dependência JWT
```bash
pip install PyJWT
```

### 2. Configurar autenticação no .env
```env
API_USUARIO=admin
API_SENHA=sua_senha_forte_aqui
API_JWT_SECRET=chave_secreta_aleatoria
API_JWT_EXPIRA_HORAS=72
```

Para gerar a chave JWT:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 3. Iniciar o servidor
```bash
uvicorn src.webhook_server:app --host 0.0.0.0 --port 8000
```

### 4. Testar a API
```bash
# Login
curl.exe -X POST http://localhost:8000/auth/login -H "Content-Type: application/json" -d "{\"usuario\":\"admin\",\"senha\":\"sua_senha\"}"

# Dashboard (use o token retornado)
curl.exe http://localhost:8000/api/dashboard -H "Authorization: Bearer SEU_TOKEN"

# Listar faturas
curl.exe http://localhost:8000/api/faturas -H "Authorization: Bearer SEU_TOKEN"

# Listar empresas
curl.exe http://localhost:8000/api/empresas -H "Authorization: Bearer SEU_TOKEN"
```

## Setup do App Mobile

### Pré-requisitos
- Node.js 18+
- npm ou yarn
- Expo CLI: `npm install -g expo-cli`
- EAS CLI (para build): `npm install -g eas-cli`
- Conta Expo (grátis): https://expo.dev/signup

### 1. Instalar dependências
```bash
cd mobile
npm install
```

### 2. Configurar URL do servidor
Edite `mobile/src/services/api.ts` e altere a `BASE_URL`:
```typescript
const BASE_URL = "http://SEU_IP:8000";  // IP da VPS ou rede local
```

### 3. Testar no celular (modo dev)
```bash
npx expo start
```
Escaneie o QR code com o app **Expo Go** no Android.

### 4. Gerar APK para instalar
```bash
# Login no Expo (uma vez)
eas login

# Build do APK
eas build -p android --profile preview
```
O APK será gerado na nuvem do Expo e você receberá o link para download.

### 5. Instalar no Android
- Baixe o APK no celular
- Ative "Instalar de fontes desconhecidas" nas configurações
- Instale e abra o app

## Estrutura do App

```
mobile/
├── src/
│   ├── App.tsx                 # Entry point
│   ├── screens/
│   │   ├── LoginScreen.tsx     # Tela de login
│   │   ├── DashboardScreen.tsx # Dashboard com KPIs
│   │   ├── FaturasScreen.tsx   # Lista e detalhes de faturas
│   │   └── EmpresasScreen.tsx  # Lista e edição de empresas
│   ├── services/
│   │   └── api.ts              # Comunicação com backend
│   └── navigation/
│       └── AppNavigator.tsx    # Tab navigation
├── app.json                    # Config Expo
├── eas.json                    # Config build (APK)
└── package.json
```

## Endpoints da API

| Método | Rota | Auth | Descrição |
|--------|------|------|-----------|
| POST | /auth/login | Não | Login → JWT |
| GET | /api/dashboard | Sim | Resumo do dia |
| GET | /api/faturas | Sim | Listar faturas |
| GET | /api/faturas/{id} | Sim | Detalhe fatura |
| POST | /api/faturas | Sim | Criar fatura |
| POST | /api/faturas/{id}/cancel | Sim | Cancelar fatura |
| GET | /api/nfse | Sim | Listar NFS-e |
| POST | /api/nfse/{id}/emitir | Sim | Emitir NFS-e |
| POST | /api/nfse/{id}/reenviar | Sim | Reenviar e-mail |
| GET | /api/empresas | Sim | Listar empresas |
| GET | /api/empresas/{cnpj} | Sim | Detalhe empresa |
| PUT | /api/empresas/{cnpj} | Sim | Editar empresa |
