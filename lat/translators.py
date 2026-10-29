"""Translation providers. Each takes ISO-style codes from languages.py.

To add a provider: subclass Translator, set name/fields, implement translate(),
and append it to PROVIDERS.
"""

import requests

from . import languages


class TranslationError(Exception):
    pass


class Translator:
    name = ""
    description = ""
    # (config key, label, secret?) shown in the API settings dialog
    fields = []
    whisper_task = "transcribe"  # "translate" = Whisper does it itself

    def __init__(self, cfg=None):
        self.cfg = cfg or {}
        self.http = requests.Session()
        self.http.headers["User-Agent"] = "LiveAudioTranslator/1.0"

    def get(self, key):
        v = (self.cfg.get(key) or "").strip()
        return v

    def require(self, *keys):
        for k in keys:
            if not self.get(k):
                label = next((f[1] for f in self.fields if f[0] == k), k)
                raise TranslationError(f"{self.name}: set '{label}' in API settings")

    def translate(self, text, source, target):
        raise NotImplementedError

    @staticmethod
    def _check(r):
        if r.status_code >= 400:
            try:
                body = r.json()
                msg = body.get("message") or body.get("error") or body
            except Exception:
                msg = r.text[:200]
            raise TranslationError(f"HTTP {r.status_code}: {msg}")
        return r


class GoogleFree(Translator):
    name = "Google Translate (built-in, free)"
    description = "Works out of the box, no key needed. Uses Google's public web endpoint."

    def translate(self, text, source, target):
        r = self.http.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": source or "auto", "tl": target, "dt": "t", "q": text},
            timeout=8,
        )
        self._check(r)
        data = r.json()
        return "".join(part[0] for part in data[0] if part and part[0])


class DeepL(Translator):
    name = "DeepL API"
    description = "Needs a DeepL API key (free keys end in ':fx'). deepl.com/pro-api"
    fields = [("deepl_key", "DeepL API key", True), ("deepl_formality", "Formality (default/more/less/prefer_more/prefer_less)", False)]

    TARGET_MAP = {"en": "EN-US", "en-GB": "EN-GB", "pt": "PT-PT", "pt-BR": "PT-BR",
                  "zh-CN": "ZH-HANS", "zh-TW": "ZH-HANT", "no": "NB"}

    def translate(self, text, source, target):
        self.require("deepl_key")
        key = self.get("deepl_key")
        host = "api-free.deepl.com" if key.endswith(":fx") else "api.deepl.com"
        body = {"text": [text], "target_lang": self.TARGET_MAP.get(target, languages.base(target).upper())}
        if source:
            src = languages.base(source).upper()
            body["source_lang"] = "NB" if src == "NO" else src
        formality = self.get("deepl_formality")
        if formality and formality != "default":
            body["formality"] = formality
        r = self.http.post(f"https://{host}/v2/translate", json=body, timeout=10,
                           headers={"Authorization": f"DeepL-Auth-Key {key}"})
        if r.status_code == 400 and "source_lang" in body:
            # DeepL rejects source languages it doesn't support; let it auto-detect.
            body.pop("source_lang")
            r = self.http.post(f"https://{host}/v2/translate", json=body, timeout=10,
                               headers={"Authorization": f"DeepL-Auth-Key {key}"})
        if r.status_code == 456:
            raise TranslationError("DeepL: monthly character quota used up")
        self._check(r)
        return r.json()["translations"][0]["text"]


class GoogleCloud(Translator):
    name = "Google Cloud Translation API"
    description = "Official Google API (v2, API key). console.cloud.google.com"
    fields = [("gcloud_key", "Google Cloud API key", True)]

    def translate(self, text, source, target):
        self.require("gcloud_key")
        body = {"q": text, "target": target, "format": "text"}
        if source:
            body["source"] = source
        r = self.http.post("https://translation.googleapis.com/language/translate/v2",
                           params={"key": self.get("gcloud_key")}, json=body, timeout=10)
        self._check(r)
        import html
        return html.unescape(r.json()["data"]["translations"][0]["translatedText"])


class Microsoft(Translator):
    name = "Microsoft Azure Translator"
    description = "Azure AI Translator resource key + region (e.g. westeurope)."
    fields = [("azure_key", "Azure Translator key", True), ("azure_region", "Region (e.g. eastus, global)", False)]

    CODE_MAP = {"zh-CN": "zh-Hans", "zh-TW": "zh-Hant", "no": "nb", "tl": "fil", "pt-BR": "pt", "pt": "pt-pt", "en-GB": "en"}

    def translate(self, text, source, target):
        self.require("azure_key")
        params = {"api-version": "3.0", "to": self.CODE_MAP.get(target, target)}
        if source:
            params["from"] = self.CODE_MAP.get(source, source)
        headers = {"Ocp-Apim-Subscription-Key": self.get("azure_key")}
        if self.get("azure_region"):
            headers["Ocp-Apim-Subscription-Region"] = self.get("azure_region")
        r = self.http.post("https://api.cognitive.microsofttranslator.com/translate",
                           params=params, headers=headers, json=[{"Text": text}], timeout=10)
        self._check(r)
        return r.json()[0]["translations"][0]["text"]


class LibreTranslate(Translator):
    name = "LibreTranslate"
    description = "Open-source; self-host it or use a public server. Key optional."
    fields = [("libre_url", "Server URL (e.g. http://localhost:5000)", False), ("libre_key", "API key (optional)", True)]

    def translate(self, text, source, target):
        self.require("libre_url")
        body = {"q": text, "source": languages.base(source) if source else "auto",
                "target": {"zh-CN": "zh", "zh-TW": "zt"}.get(target, languages.base(target)), "format": "text"}
        if self.get("libre_key"):
            body["api_key"] = self.get("libre_key")
        r = self.http.post(self.get("libre_url").rstrip("/") + "/translate", json=body, timeout=15)
        self._check(r)
        return r.json()["translatedText"]


class WhisperOffline(Translator):
    name = "Whisper offline (to English only)"
    description = "No internet needed. Whisper translates speech straight to English. Target must be English; not supported by the large-v3-turbo model."
    whisper_task = "translate"

    def translate(self, text, source, target):
        return text


PROVIDERS = [GoogleFree, DeepL, GoogleCloud, Microsoft, LibreTranslate, WhisperOffline]
BY_NAME = {p.name: p for p in PROVIDERS}


def create(name, cfg):
    return BY_NAME.get(name, GoogleFree)(cfg)
