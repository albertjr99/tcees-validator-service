"""
Microserviço TCEES – roda no Render (saída de rede livre).
Usa o tcees_validator.py original e devolve o dict completo esperado pelo app.py.

POST /validate
  form-data: file=<arquivo.pdf>
  Retorna: dict completo com extensao_valida, sem_senha, assinado, resultado_final, etc.

GET /health
  Retorna: {"status": "ok"}
"""

import os
import uuid
import logging
import tempfile

from flask import Flask, request, jsonify

# Importa o validador original – inclua tcees_validator.py neste repositório
from tcees_validator import validate_pdf_with_tcees

# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)

API_SECRET  = os.getenv("TCEES_API_SECRET", "")
MAX_FILE_MB = int(os.getenv("TCEES_MAX_FILE_MB", "20"))


def _auth_ok(req):
    if not API_SECRET:
        return True
    return req.headers.get("X-API-Secret") == API_SECRET


# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "tcees-validator"})


@app.post("/validate")
def validate():
    if not _auth_ok(request):
        return jsonify({
            "resultado_final": "ERRO",
            "erro": "Não autorizado.",
            "erro_codigo": "AUTH_ERROR",
        }), 401

    if "file" not in request.files:
        return jsonify({
            "resultado_final": "ERRO",
            "erro": "Nenhum arquivo enviado (campo 'file').",
            "erro_codigo": "NO_FILE",
        }), 400

    uploaded = request.files["file"]

    if not uploaded.filename.lower().endswith(".pdf"):
        return jsonify({
            "resultado_final": "ERRO",
            "erro": "Apenas arquivos PDF são aceitos.",
            "erro_codigo": "NOT_PDF",
        }), 400

    uploaded.seek(0, 2)
    size_mb = uploaded.tell() / (1024 * 1024)
    uploaded.seek(0)
    if size_mb > MAX_FILE_MB:
        return jsonify({
            "resultado_final": "ERRO",
            "erro": f"Arquivo excede {MAX_FILE_MB} MB.",
            "erro_codigo": "FILE_TOO_LARGE",
        }), 413

    # Salva temporariamente
    suffix = f"_{uuid.uuid4().hex}.pdf"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = tmp.name
        uploaded.save(tmp_path)

    log.info("Validando: %s (%.2f MB)", uploaded.filename, size_mb)

    try:
        # Chama o validador original – retorna o dict completo que o app.py espera
        resultado = validate_pdf_with_tcees(tmp_path)
        # Preserva o nome original do arquivo
        resultado["nome_arquivo"] = uploaded.filename
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    log.info("Resultado: %s | Pontuação: %s", resultado.get("resultado_final"), resultado.get("pontuacao"))
    return jsonify(resultado), 200


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
