import base64
import os

import msgpack
import requests
from flask import Flask, Response, jsonify, request, send_from_directory

app = Flask(__name__, static_folder="static", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024  # 60 MB por subida

FISH_API = "https://api.fish.audio"
# Modelo del plan gratis de Fish Audio (según su documentación)
FISH_MODEL = os.environ.get("FISH_MODEL", "s2.1-pro-free")


def get_key():
    """La clave llega desde la página en cada petición. El servidor no la guarda ni la registra."""
    return request.headers.get("X-Fish-Key", "").strip()


def explain(status, text):
    if status == 401:
        return "La API key no es válida. Revísala en fish.audio/app/api-keys."
    if status == 402:
        return "Te quedaste sin créditos o el plan no permite esta acción."
    if status == 503:
        return "El servicio de Fish Audio está saturado. Intenta de nuevo en un momento."
    return f"Fish Audio respondió con error {status}: {text[:300]}"


@app.errorhandler(Exception)
def manejar_error(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return jsonify(error=e.description), e.code
    app.logger.exception("Error inesperado")
    return jsonify(error="Error interno del servidor. Intenta con un texto más corto."), 500


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/transcribir")
def transcribir_page():
    return send_from_directory(app.static_folder, "transcribir.html")


@app.post("/api/transcribe")
def transcribe():
    key = get_key()
    if not key:
        return jsonify(error="Pega tu API key de Fish Audio en el cuadro de arriba."), 401

    f = request.files.get("audio")
    if not f:
        return jsonify(error="Sube un audio para transcribir."), 400

    # Texto limpio, tal como lo pide Fish para clonar: sin marcas de hablante ni etiquetas [risas].
    data = {"ignore_timestamps": "true", "tag_audio_events": "false", "diarize": "false"}
    lang = (request.form.get("language") or "").strip().lower()
    if lang and lang != "auto":
        data["language"] = lang

    try:
        r = requests.post(
            f"{FISH_API}/v1/asr",
            headers={"Authorization": f"Bearer {key}", "model": "transcribe-1-pro"},
            data=data,
            files={"audio": (f.filename or "audio.mp3", f.stream, f.mimetype or "audio/mpeg")},
            timeout=300,
        )
    except requests.RequestException as e:
        return jsonify(error=f"No se pudo conectar con Fish Audio: {e}"), 502

    if r.status_code != 200:
        if r.status_code == 400:
            msg = "Fish Audio no pudo leer ese audio. Prueba con WAV, MP3, M4A o FLAC."
        elif r.status_code == 402:
            msg = "Tu cuenta no tiene saldo para transcribir. Añade crédito en fish.audio o escribe el texto a mano."
        elif r.status_code == 413:
            msg = "El audio pesa demasiado. Usa uno más corto o en MP3."
        elif r.status_code == 429:
            msg = "Hay demasiadas peticiones a la vez. Espera unos segundos y reintenta."
        else:
            msg = explain(r.status_code, r.text)
        return jsonify(error=msg), r.status_code

    j = r.json()
    return jsonify(
        text=(j.get("text") or "").strip(),
        duration=j.get("duration"),
        language=j.get("language"),
    )


@app.post("/api/voices")
def create_voice():
    key = get_key()
    if not key:
        return jsonify(error="Pega tu API key de Fish Audio en el cuadro de arriba."), 401

    files = request.files.getlist("audios")
    if not files:
        return jsonify(error="Sube al menos un audio de tu voz."), 400

    data = {
        "type": "tts",
        "title": (request.form.get("title") or "Mi voz").strip()[:80],
        "train_mode": "fast",
        "visibility": "private",
        "enhance_audio_quality": "true",
    }
    # La transcripción es opcional: si no se envía, Fish Audio la detecta sola.
    # Solo se acepta con un único audio para que el texto corresponda a ese audio.
    transcript = (request.form.get("texts") or "").strip()
    if transcript and len(files) == 1:
        data["texts"] = transcript

    multipart = [
        ("voices", (f.filename or "voz.mp3", f.stream, f.mimetype or "audio/mpeg"))
        for f in files
    ]

    try:
        r = requests.post(
            f"{FISH_API}/model",
            headers={"Authorization": f"Bearer {key}"},
            data=data,
            files=multipart,
            timeout=180,
        )
    except requests.RequestException as e:
        return jsonify(error=f"No se pudo conectar con Fish Audio: {e}"), 502

    if r.status_code == 403:
        # Cuentas gratis: Fish Audio no deja guardar voces por API sin saldo.
        return jsonify(
            error="Tu cuenta gratis no puede guardar voces en Fish Audio.",
            code="model_limit",
            detail=r.text[:300],
        ), 403
    if r.status_code not in (200, 201):
        return jsonify(error=explain(r.status_code, r.text)), r.status_code

    j = r.json()
    return jsonify(id=j.get("_id"), title=j.get("title"), state=j.get("state"))


@app.post("/api/tts")
def tts():
    key = get_key()
    if not key:
        return jsonify(error="Pega tu API key de Fish Audio en el cuadro de arriba."), 401

    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    voice_id = (body.get("reference_id") or "").strip()
    ref_b64 = body.get("reference_audio") or ""
    ref_text = (body.get("reference_text") or "").strip()
    if not text:
        return jsonify(error="Escribe el texto que quieres convertir en voz."), 400
    if not voice_id and not ref_b64:
        return jsonify(error="Elige una voz primero."), 400

    try:
        speed = min(2.0, max(0.5, float(body.get("speed", 1))))
    except (TypeError, ValueError):
        speed = 1.0

    payload = {
        "text": text,
        "format": "mp3",
        "normalize": True,
        "prosody": {"speed": speed, "volume": 0, "normalize_loudness": True},
    }
    lang = (body.get("language") or "").strip()
    if lang and lang != "auto":
        payload["language"] = lang

    headers = {"Authorization": f"Bearer {key}", "model": FISH_MODEL}
    req = {"timeout": 300}
    if ref_b64:
        # Clonación al vuelo: el audio de referencia viaja en cada petición (MessagePack).
        if not ref_text:
            return jsonify(error="Falta el texto que dice el audio de referencia."), 400
        try:
            audio_bytes = base64.b64decode(ref_b64)
        except Exception:
            return jsonify(error="El audio de referencia no es válido."), 400
        payload["references"] = [{"audio": audio_bytes, "text": ref_text}]
        headers["Content-Type"] = "application/msgpack"
        req["data"] = msgpack.packb(payload, use_bin_type=True)
    else:
        payload["reference_id"] = voice_id
        req["json"] = payload

    try:
        r = requests.post(f"{FISH_API}/v1/tts", headers=headers, **req)
    except requests.RequestException as e:
        return jsonify(error=f"No se pudo conectar con Fish Audio: {e}"), 502

    if r.status_code != 200:
        return jsonify(error=explain(r.status_code, r.text)), r.status_code

    return Response(r.content, mimetype="audio/mpeg")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
