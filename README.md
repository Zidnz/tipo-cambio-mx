# Tipo de cambio FIX · Pipeline ETL automatizado

[![Actualizar tipo de cambio](https://github.com/Zidnz/tipo-cambio-mx/actions/workflows/actualizar.yml/badge.svg)](https://github.com/Zidnz/tipo-cambio-mx/actions/workflows/actualizar.yml)
[![Dashboard](https://img.shields.io/badge/Dashboard-en_vivo-2fa7a7?style=flat-square)](https://zidnz.github.io/tipo-cambio-mx/)

Pipeline que todos los días hábiles descarga el **tipo de cambio FIX peso-dólar** de la API de Banxico, lo limpia, lo valida, calcula indicadores y publica un dashboard en GitHub Pages. Cada actualización es un commit con el dato del día; si Banxico no publicó nada nuevo, no hay commit.

**Dashboard:** https://zidnz.github.io/tipo-cambio-mx/

## Cómo funciona

```
GitHub Actions (cron, L-V 13:23 CDMX)
        │
        ▼
  extract   API SIE de Banxico, serie SF43718 (token como secret del repo)
        │
        ▼
  transform  "N/E" -> nulo, tipos, duplicados, orden por fecha
        │
        ▼
  validate   rango plausible, fechas únicas, sin fechas futuras
        │
        ▼
  merge      histórico + últimos 10 días (captura revisiones de Banxico)
        │
        ▼
  metrics    media móvil 20d, volatilidad 30d anualizada, rango 52 semanas, variación anual
        │
        ▼
  load       docs/data/tipo_cambio.csv + resumen.json  ->  commit solo si hubo cambio
        │
        ▼
  GitHub Pages (docs/index.html, Chart.js)
```

Decisiones de diseño:

- **Idempotente:** correr el pipeline dos veces el mismo día no genera cambios ni commits.
- **Carga incremental:** la primera corrida trae la historia desde 2015; después solo pide los últimos 10 días, por si Banxico corrige algún dato.
- **Falla de forma segura:** si la API responde algo inesperado o un valor sale de rango, el pipeline se detiene sin sobrescribir el histórico.
- **Pruebas antes de publicar:** el workflow corre `pytest` antes del ETL.

## Estructura

```
src/pipeline.py              ETL completo (extract, transform, validate, merge, metrics, load)
tests/test_pipeline.py       pruebas con una respuesta de ejemplo de la API
docs/index.html              dashboard estático
docs/data/                   datos publicados (los genera el pipeline)
.github/workflows/actualizar.yml
```

## Correrlo localmente

```bash
pip install -r requirements.txt
pytest -q
BANXICO_TOKEN=tu_token python src/pipeline.py
```

El token es gratuito: https://www.banxico.org.mx/SieAPIRest/service/v1/token

## Stack

Python · pandas · requests · pytest · GitHub Actions · GitHub Pages · Chart.js

---

Fuente de datos: Banco de México, Sistema de Información Económica (SIE). Proyecto de [Omar Gutiérrez](https://zidnz.github.io/portafolio-omar-gutierrez/).
