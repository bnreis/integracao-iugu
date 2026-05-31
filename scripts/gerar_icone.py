"""
Gera o ícone do app a partir do logo da Megasuporte.

Baixa o logo branco do site e compõe sobre um quadrado azul (cor do app),
produzindo um ícone quadrado pronto para o Expo (app + PWA).

Saída (no diretório atual):
    icon_app.png      1024x1024  -> vira mobile/assets/icon.png
    favicon_app.png    512x512   -> vira mobile/assets/favicon.png

Requer: requests (já instalado) + Pillow.
    Se faltar Pillow:  .venv/bin/pip install Pillow

Uso (na VPS, dentro de /opt/integracao-iugu):
    .venv/bin/python scripts/gerar_icone.py
"""
import io
import sys

import requests

try:
    from PIL import Image
except ImportError:
    print("Pillow nao instalado. Rode: .venv/bin/pip install Pillow")
    sys.exit(1)

# Logo branco (bom sobre fundo escuro/azul)
LOGO_URL = "https://megasuporte.com/wp-content/uploads/2024/09/logoBranco.png"
BG = (26, 86, 219)   # #1a56db — azul do app
SIZE = 1024
PAD_RATIO = 0.16     # margem ao redor do logo (16% de cada lado)


def main() -> None:
    print(f"Baixando logo: {LOGO_URL}")
    # User-Agent de navegador: o site (WordPress) responde 406 sem isso.
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/png,image/*,*/*;q=0.8",
    }
    resp = requests.get(LOGO_URL, headers=headers, timeout=30)
    resp.raise_for_status()
    logo = Image.open(io.BytesIO(resp.content)).convert("RGBA")
    print(f"Logo original: {logo.width}x{logo.height}")

    # Canvas quadrado azul
    canvas = Image.new("RGBA", (SIZE, SIZE), BG + (255,))

    # Escala o logo para caber na área útil (com margem), preservando proporção
    max_lado = int(SIZE * (1 - 2 * PAD_RATIO))
    ratio = min(max_lado / logo.width, max_lado / logo.height)
    novo = (max(1, int(logo.width * ratio)), max(1, int(logo.height * ratio)))
    logo_red = logo.resize(novo, Image.LANCZOS)

    # Centraliza
    pos = ((SIZE - novo[0]) // 2, (SIZE - novo[1]) // 2)
    canvas.paste(logo_red, pos, logo_red)

    # Ícone do app (opaco — ícones de app são quadrados sem transparência)
    canvas.convert("RGB").save("icon_app.png", "PNG")
    # Favicon menor
    canvas.resize((512, 512), Image.LANCZOS).convert("RGB").save("favicon_app.png", "PNG")

    print("OK -> icon_app.png (1024x1024) e favicon_app.png (512x512)")


if __name__ == "__main__":
    main()
