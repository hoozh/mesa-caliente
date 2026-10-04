# Mesa Caliente v2

Barrido diario de noticias de poker. Todos los días, de forma automática:

1. Lee las fuentes de `config/fuentes.json` (RSS cuando existe, si no la portada de noticias). Esta parte no usa inteligencia artificial.
2. Descarta los titulares que ya se vieron en corridas anteriores.
3. Hace **una sola consulta** al modelo de Claude más económico (por defecto `claude-haiku-4-5`) con titulares, fecha, fuente y primera línea. El modelo elige hasta 12 hechos, los agrupa, los clasifica como **Confirmado**, **En discusión** o **Rumor** y escribe un resumen corto. También llena la sección **Escena argentina y latinoamericana**.
4. Guarda el resultado del día en `data/dias/AAAA-MM-DD.json` y regenera la página `docs/index.html`, que publica GitHub Pages.

El costo estimado es de unos US$ 0,02 a 0,03 por día (alrededor de US$ 1 por mes). El pie de la página muestra los tokens y el costo de la última corrida y del mes.

---

## Puesta en marcha (una sola vez)

### 1. Cargar la clave de Anthropic como secreto

1. Consiga una clave de API en <https://console.anthropic.com/> (menú **API Keys** → **Create Key**). Copie la clave completa (empieza con `sk-ant-`).
2. En GitHub, abra este repositorio y vaya a **Settings** (Configuración).
3. En el menú de la izquierda, entre a **Secrets and variables** → **Actions**.
4. Pulse **New repository secret**.
5. En **Name** escriba exactamente: `ANTHROPIC_API_KEY`
6. En **Secret** pegue la clave y pulse **Add secret**.

Si la clave falta o es inválida, la página igual se publica y ese día queda registrado como "Corrida fallida" con el motivo.

### 2. Activar la página (GitHub Pages)

1. En el repositorio, vaya a **Settings** → **Pages**.
2. En **Build and deployment** → **Source**, elija **Deploy from a branch**.
3. En **Branch**, elija `main` y la carpeta `/docs`. Pulse **Save**.
4. Después de uno o dos minutos, la misma pantalla muestra la dirección pública (algo como `https://USUARIO.github.io/mesa-caliente/`).

### 3. Permitir que el flujo guarde los resultados

1. Vaya a **Settings** → **Actions** → **General**.
2. En **Workflow permissions**, marque **Read and write permissions** y pulse **Save**.

---

## Uso diario

### Corrida automática

El flujo **Barrido diario** corre solo todos los días a las **12:45 UTC** (9:45 en Argentina). No hace falta hacer nada.

### Correrlo a mano

1. Abra la pestaña **Actions** del repositorio.
2. A la izquierda, elija **Barrido diario**.
3. Pulse **Run workflow** → **Run workflow**.
4. En unos minutos aparece un nuevo commit en `main` y la página se actualiza. Si ya hubo una corrida ese día, la nueva se agrega al mismo día sin borrar la anterior.

### Cambiar el modelo (opcional)

Por defecto se usa `claude-haiku-4-5`. Para usar otro, en **Settings** → **Secrets and variables** → **Actions** → pestaña **Variables**, cree la variable `MESA_MODELO` con el nombre del modelo. Si el modelo no figura en `config/ajustes.json`, agregue allí su precio para que el costo estimado sea correcto.

---

## Agregar o quitar una fuente

Todas las fuentes están en `config/fuentes.json`. Se puede editar desde GitHub mismo (abra el archivo y pulse el lápiz ✏️).

**Para agregar una fuente**, copie un bloque existente, péguelo dentro de la lista (cuidando las comas) y cambie:

- `nombre`: cómo aparecerá en la página.
- `url`: la dirección del RSS. Para encontrarla, pruebe agregando `/feed/` o `/rss/` al final del sitio. Si no hay RSS, ponga la página de noticias.
- `tipo`: `rss` si es un RSS, `html` si es una página de noticias.
- `idioma`: `es`, `en` o `pt`.
- `region`: `internacional`, `latam`, `argentina`, `espana` u otra.
- `estado`: `activa`. Deje `fallos_consecutivos` en `0` y `ultimo_ok` en `null`.

Ejemplo:

```json
{"nombre": "Nuevo Medio", "url": "https://nuevomedio.com/feed/", "portada": "https://nuevomedio.com/", "tipo": "rss", "idioma": "es", "region": "latam", "estado": "activa", "fallos_consecutivos": 0, "ultimo_ok": null}
```

Para páginas `html` se puede agregar `"incluir"` o `"excluir"` con un patrón de direcciones, para tomar solo las notas (ver los ejemplos de WSOP.com y Código Poker).

**Para quitar una fuente**, borre su bloque completo (y la coma sobrante).

**Para pausar una fuente a mano**, cambie su `estado` a `en_pausa`.

> Los sitios de GipsyTeam (gipsyteam.com, gipsyteam.com.br, gipsyteam.ru, latam.gipsyteam.com y cualquier subdominio) están prohibidos: el programa nunca los consulta, nunca los cita y nunca los propone como candidatos, aunque alguien los agregue a la lista.

---

## Cómo funciona por dentro

| Parte | Archivo |
|---|---|
| Fuentes y su salud | `config/fuentes.json` |
| Topes fijos y precios | `config/ajustes.json` |
| Un archivo por día | `data/dias/AAAA-MM-DD.json` |
| Direcciones ya vistas | `data/vistos.json` |
| Fuentes candidatas | `data/candidatas.json` |
| Tokens y costo por corrida | `data/costos.json` |
| Diseño de la página | `templates/index.html.j2` y `templates/estilo.css` |
| Página publicada | `docs/index.html` |

- **Salud de fuentes**: si una fuente falla 3 días seguidos (error, bloqueo, 403, sin contenido), pasa a `en_pausa`. Las pausadas se reintentan una vez por semana y vuelven a `activa` si responden. La página las lista en "Salud de fuentes" con el motivo.
- **Topes fijos** (en `config/ajustes.json`): 120 titulares enviados, 6 artículos abiertos como máximo, 3000 tokens de salida, 12 tarjetas.
- **Artículos abiertos**: solo se abre el texto de una nota cuando su titular no trae primera línea suficiente (como máximo 6 por corrida).
- **Candidatas**: el programa cuenta los dominios enlazados dentro de los artículos que abre. Si un dominio aparece en al menos 3 artículos distintos en 14 días, no está en las fuentes y no es una red social, se agrega a "Fuentes candidatas". Nunca se agrega solo a la lista de fuentes y nunca se borra solo.
- **Robustez**: una corrida solo escribe el archivo de su propio día; nunca modifica días anteriores. Si el modelo falla, el día queda como "Corrida fallida" con el motivo y los titulares se vuelven a intentar al día siguiente.
- **Controles sobre el resumen**: el programa revisa lo que escribe el modelo. Conserva el premio que nombra la fuente (brazalete para la WSOP, anillo para el WSOP Circuit, trofeo), agrega la nacionalidad cuando el titular o la primera línea la indican y, en la escena latina, marca "[nacionalidad a confirmar]" si nadie la indica. Además, el modelo recibe los títulos de las tarjetas de los últimos 3 días (`dias_titulos_previos` en `config/ajustes.json`) y no repite un hecho salvo que haya un desarrollo nuevo; en ese caso la tarjeta muestra "Qué cambió". Lo que el programa corrige o descarta queda anotado en el archivo del día (campo `controles`).
- **Diseño**: para cambiar el aspecto basta con editar `templates/`. La recolección no se toca.

### Comandos útiles (en una computadora con Python 3.10 o superior)

```bash
pip install -r requirements.txt
python -m pytest -q                         # pruebas (sin red ni API)
python -m mesa.corrida --solo-recoleccion   # prueba qué fuentes responden, sin guardar nada
python -m mesa.pagina                       # regenera docs/index.html con los datos actuales
python -m mesa.corrida                      # corrida completa (necesita ANTHROPIC_API_KEY)
```

El historial de la versión anterior (`mesa_caliente_historial.json`, 21 bloques y 159 tarjetas) ya está importado en `data/dias/`. El comando `python -m mesa.importar` no pisa días existentes.
