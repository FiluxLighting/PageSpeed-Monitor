# PageSpeed Monitor

Herramienta de monitorización automática de **Core Web Vitals** y métricas de rendimiento para múltiples URLs, usando la [PageSpeed Insights API v5](https://developers.google.com/speed/docs/insights/v5/about) de Google.

## Características

- ✅ Consultas automáticas via **GitHub Actions** (sin servidores propios)
- ✅ Soporte para **mobile** y **desktop** simultáneamente
- ✅ Histórico acumulativo en **CSV** (commitado en el repo)
- ✅ Informe **HTML interactivo** con gráficas de evolución temporal
- ✅ Métricas clave: Score, LCP, CLS, FCP, TBT, TTI, Speed Index
- ✅ Indicadores de color verde/amarillo/rojo según umbrales de Google
- ✅ Comparativa **Δ delta** vs. medición anterior

---

## Configuración inicial

### 1. Editar las URLs a monitorizar

Abre [`config/urls.json`](config/urls.json) y ajusta la lista de URLs:

```json
{
  "api_key": "",
  "strategies": ["mobile", "desktop"],
  "urls": [
    { "label": "Home",     "url": "https://tusitio.com/" },
    { "label": "Blog",     "url": "https://tusitio.com/blog/" },
    { "label": "Contacto", "url": "https://tusitio.com/contacto/" }
  ]
}
```

> ℹ️ En `config/urls.json` se recomienda dejar `"api_key": ""`. Para local, crea un archivo `.env` con `PAGESPEED_API_KEY=tu_clave` (está en `.gitignore` para no subirlo a git). Para GitHub Actions, usa un secreto (paso 3).

### 2. Obtener una API Key gratuita (recomendado)

1. Ve a [Google Cloud Console](https://console.cloud.google.com/)
2. Crea un proyecto (o usa uno existente)
3. Habilita la **PageSpeed Insights API**: Menú > APIs y servicios > Habilitar APIs > buscar "PageSpeed"
4. Crea una clave de API: APIs y servicios > Credenciales > Crear credenciales > Clave de API
5. (Opcional) Restringe la clave a la PageSpeed API para mayor seguridad

### 3. Configurar el secreto en GitHub

1. Ve a tu repositorio > **Settings** > **Secrets and variables** > **Actions**
2. Crea un nuevo secreto llamado `PAGESPEED_API_KEY` con el valor de tu clave

### 4. Configurar la frecuencia de ejecución

Configurado por defecto para ejecutarse **todos los días** a las 8:00 UTC en [`.github/workflows/pagespeed.yml`](.github/workflows/pagespeed.yml):

```yaml
schedule:
  - cron: "0 8 * * *"   # Cada día a las 8:00 UTC
  # - cron: "0 8 * * 1"   # Opcional: solo lunes
```

---

## Ejecución local

No requiere librerías externas (utiliza únicamente la librería estándar de Python 3):

```bash
# Ejecutar desde la raíz del proyecto
python3 scripts/run_pagespeed.py
```

El script generará o actualizará:
- `data/history.csv` — histórico de todas las mediciones
- `data/latest_audits.json` — diagnósticos y mejoras técnicas detectadas por Lighthouse
- `index.html` — dashboard visual publicado automáticamente en GitHub Pages
- `reports/report.html` — copia del informe en reports

---

## Estructura del proyecto

```
pagespeed-monitor/
├── .github/workflows/pagespeed.yml  # Automatización periódica con GitHub Actions
├── config/urls.json                 # URLs y estrategias configuradas
├── scripts/run_pagespeed.py         # Script principal (API Google + Reporte)
├── data/history.csv                 # Histórico CSV acumulado (auto-generado)
├── data/latest_audits.json          # Diagnósticos técnicos detallados (auto-generado)
├── index.html                       # Dashboard interactivo para GitHub Pages
├── reports/report.html              # Copia de respaldo del informe HTML
└── .nojekyll                        # Evita que Jekyll bloquee archivos estáticos en Pages
```

---

## Métricas capturadas

| Métrica | Descripción | Umbral bueno |
|---|---|---|
| **Performance Score** | Puntuación global Lighthouse (0-100) | ≥ 90 |
| **LCP** | Largest Contentful Paint | ≤ 2.5s |
| **CLS** | Cumulative Layout Shift | ≤ 0.1 |
| **FCP** | First Contentful Paint | ≤ 1.8s |
| **TBT** | Total Blocking Time | ≤ 200ms |
| **Speed Index** | Speed Index | ≤ 3.4s |
| **TTI** | Time to Interactive | ≤ 3.8s |
