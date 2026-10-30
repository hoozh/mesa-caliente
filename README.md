# Mesa Caliente v2

Barrido diario de noticias de poker. Todos los días, de forma automática:

1. Lee las fuentes de `config/fuentes.json` (RSS cuando existe, si no la portada de noticias). Esta parte no usa inteligencia artificial.
2. Toma solo lo publicado desde la última corrida exitosa y descarta lo que ya se vio en corridas anteriores.
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

GitHub a veces atrasa o salta los horarios programados. Por eso hay dos horarios de respaldo, a las **13:25** y a las **15:45 UTC**: si ese día ya hubo una corrida exitosa, el respaldo no hace nada (no consulta al modelo ni cambia la página). Las corridas manuales siempre corren.

### Correrlo a mano

1. Abra la pestaña **Actions** del repositorio.
2. A la izquierda, elija **Barrido diario**.
3. Pulse **Run workflow** → **Run workflow**.
4. En unos minutos aparece un nuevo commit en `main` y la página se actualiza. Si ya hubo una corrida ese día, la nueva se agrega al mismo día sin borrar la anterior.

### Entrada opcional «origen» (para corridas que dispara otro sistema)

Al pulsar **Run workflow** aparece un campo opcional, **origen**:

- **Vacío** (lo normal al correrlo a mano): la corrida siempre se hace, aunque ese día ya haya habido otra.
- **`externo`**: pensado para un sistema externo (otro programa, un recordatorio automático) que dispara el flujo. Si ese día (en hora UTC) ya hubo una corrida exitosa, la corrida termina sin consultar al modelo y sin cambiar nada, igual que los horarios de respaldo. Si todavía no hubo ninguna, corre normalmente y en la página figura como "Corrida externa". No importan las mayúsculas ni los espacios.

Cualquier otro valor se trata como una corrida manual.

### Cómo se arma cada corrida: selección y redacción (desde el 19 oct 2026)

Cada corrida hace **dos llamadas cortas** al modelo:

1. **Selección** (solo titulares y primera línea, sin extractos): el modelo elige y agrupa todos los hechos relevantes, hasta `max_hechos_general` (14) y `max_hechos_latam` (6). Recibe la ventana de la corrida (`max_titulares_enviados`, 120) y, además, las notas **pendientes** de las últimas 48 horas que todavía no se publicaron (`ventana_pendientes_horas`, `max_pendientes_enviados`: 40), marcadas "(pendiente)". Las notas de temas calientes no pasan por la selección: van directo a la redacción.
2. **Apertura de artículos**: el programa abre el artículo de cada hecho elegido (una nota por hecho, de un medio u oficial si la hay; como máximo `max_articulos_abiertos`, 20, con `max_caracteres_extracto`, 1200).
3. **Redacción**: el modelo recibe solo las notas elegidas, con su extracto, y escribe una tarjeta por hecho. Como escribe con el texto real de la nota, los controles pueden rastrear cada cifra y cada nombre, y se descartan menos tarjetas por "sin respaldo".

Si la selección falla (error o respuesta inválida), la redacción recibe la ventana como antes y la página lo indica. Si la salida de la redacción se corta por el tope de tokens, se conservan las tarjetas que llegaron completas.

Esto reemplaza a la antigua "recuperación" (una segunda llamada que se hacía solo si el día no llegaba a la meta y que a menudo se cortaba por falta de tokens).

### Meta diaria de 10 notas

El objetivo es publicar 10 tarjetas por día, sumando todas las corridas del día (las tarjetas de la escena latina cuentan, y cada tema caliente cuenta una vez). La meta es solo una medida del pie: **la selección no la conoce** y elige todos los hechos relevantes que haya, sin apuntar a un número. Desde el 20 de octubre de 2026 ya no hay "segundo nivel" (relleno con torneos menores para completar la meta); las tarjetas viejas con esa etiqueta se siguen mostrando como estaban.

Las reglas fijas no cambian nunca: se siguen descartando las promociones, los avances de día 1 fuera de `config/torneos_relevantes.json`, las tarjetas sin dato concreto, los datos que no figuran en el texto enviado al modelo y las repeticiones sin dato nuevo. **Nada se inventa ni se completa para llegar a la meta.** Si no se llega, se publica lo que hay y el pie dice "N notas hoy" con el motivo: titulares que la recolección dejó fuera, cuántos hechos eligió la selección y los descartes por regla y por fuente.

Topes de costo (`config/ajustes.json`): `max_tokens_seleccion` (1200), `max_tokens_salida` (5500, redacción; era 4500 hasta que la redacción empezó a devolver la ficha del registro). Si la salida igual se corta, se conservan las tarjetas completas y el pie de la página y `controles` avisan cuántas tarjetas se perdieron (hechos elegidos sin tarjeta completa) y `max_costo_usd_por_corrida` (US$ 0,05): si hace falta, la redacción achica su salida para no pasar el tope. El costo de cada llamada se guarda aparte en `data/costos.json` (`"tipo": "seleccion"` y `"tipo": "redaccion"`). Con Haiku y las notas del 19 de octubre de 2026, la estimación es de unos US$ 0,009 para la selección y US$ 0,023 a 0,028 para la redacción: alrededor de US$ 0,03 por corrida.

### Cambiar el modelo (opcional)

Por defecto se usa `claude-haiku-4-5`. Para usar otro, en **Settings** → **Secrets and variables** → **Actions** → pestaña **Variables**, cree la variable `MESA_MODELO` con el nombre del modelo. Si el modelo no figura en `config/ajustes.json`, agregue allí su precio para que el costo estimado sea correcto.

---

## Registro permanente, cambios en salas y brief semanal (desde el 30 oct 2026)

Mesa Caliente deja un registro para el resto de SEI, **sin llamadas extra en el barrido**: la misma llamada de redacción devuelve, junto con cada tarjeta, una ficha breve, y el programa la verifica contra el texto enviado al modelo. Lo que no figura ahí queda vacío.

### `data/registro/hechos.jsonl`

Una línea JSON por tarjeta publicada (también las de temas calientes), con estos campos:

| Campo | Contenido |
|---|---|
| `fecha`, `id` | Día de la corrida e identificador estable (`AAAA-MM-DD-xxxxxxxx`) |
| `titulo`, `clasificacion` | Los de la tarjeta publicada |
| `tipo` | `resultado`, `anuncio_evento`, `industria_polemica`, `software`, `obituario` u `otro` |
| `seccion` | `general`, `latam` o `tema` (tarjeta de tema caliente) |
| `salas` | Salas nombradas en las notas |
| `jugadores` | `[{"nombre", "pais"}]`. El país solo si una nota de la fuente lo vincula de forma explícita con esa persona («el argentino X», «Argentine pro X», «X (Argentina)», «X, de Brasil»). Si el país aparece sin ese vínculo, queda vacío con `pais_pendiente` (`valor` candidato y `fuente`); cuando una línea posterior trae a ese jugador con ese país explícito, se confirma (`pais` lleno y `pais_confirmado_por`: fecha e id del hecho). |
| `torneo`, `pais_evento` | Solo si están escritos en las notas |
| `temas_calientes` | Temas cuyas palabras clave aparecen en la tarjeta o sus notas |
| `fuentes` | `[{"nombre", "url", "clase"}]` |

**Historial**: para cargar una sola vez lo publicado desde el 21 sep 2026 (hace falta el modelo, porque las tarjetas viejas no tienen esos campos):

1. Abra **Actions** → **Cargar historial del registro** → **Run workflow** (solo tiene botón, no corre sola).
2. Usa la clave `ANTHROPIC_API_KEY` del repositorio, registra el costo en `data/costos.json` (`"tipo": "historial"`, unos US$ 0,28) y guarda en `main`.
3. Si el historial ya está cargado, no llama al modelo ni cambia nada. Si algún lote falló, al volver a correrlo carga solo lo que falta.

En una computadora con Python: `python -m mesa.historial_registro` informa el costo estimado sin llamar al modelo; con `--confirmar`, carga.

En el historial, el país de un jugador queda casi siempre pendiente: el resumen publicado no es texto de la fuente, y antes del 7 oct no se guardaba el titular original.

### Cambios en salas

Las salas vigiladas están en `config/salas_vigiladas.json` (se edita con el lápiz ✏️; cada sala tiene `alias` para otras formas de nombrarla; Ignition figura solo como «Ignition Poker», nunca «Ignition» suelto). Si una nota trae un cambio de una sala vigilada sobre bonos y promociones, rake y rakeback, HUD y software permitido, pagos y retiros, app y plataformas, licencia o países, o series y torneos insignia, se agrega a `data/registro/cambios_salas.jsonl` (`fecha`, `sala`, `tema`, `dato`, `fuente`).

- El `dato` debe estar copiado **tal cual** del texto de la nota (en su idioma) y la sala debe estar nombrada en esa nota; si no, se descarta y queda anotado en `controles`.
- Las promociones siguen fuera de las tarjetas. La selección las puede elegir en una sección aparte, «salas» (hasta `max_hechos_salas`, 6), que solo alimenta este registro.
- La página muestra la sección **Cambios en salas** con los últimos 30 días (`dias_cambios_salas_en_pagina`).

### Brief semanal

El flujo **Brief semanal** corre los **lunes a las 12:30 UTC** (y con el botón **Run workflow**, con una semana opcional `AAAA-Wnn`). Lee el registro de la semana anterior (lunes a domingo) y hace **una sola llamada** al modelo. Trae: temas de la semana, cambios en salas, jugadores y torneos latinos destacados, fuentes nuevas que rindieron e ideas de artículos.

- **Ideas de artículos** y **fuentes nuevas** las decide el programa con el registro. Es idea, con su motivo, un hecho o tema que en la semana tuvo 3 o más fuentes distintas, o apareció en 2 o más días (el mismo tema caliente, el mismo torneo o un título casi igual), o un tema caliente que siguió 2 semanas seguidas o más. Una fuente es nueva si aparece por primera vez en el registro esa semana. El modelo solo agrega una oración a cada idea.
- Cada texto del modelo debe citar líneas del registro y se quitan las oraciones con datos que no figuren en ellas.
- La página principal tiene un enlace **Brief semanal** a `docs/semanal/`.
- Guarda `data/semanal/AAAA-Wnn.json` y `.md`, publica `docs/semanal/` (https://hoozh.github.io/mesa-caliente/semanal/) y abre el issue «Brief semanal AAAA-Wnn» con el texto (si ya existe, no abre otro). El costo queda en `data/costos.json` con `"tipo": "brief"`.

Las secciones nuevas se dibujan con plantillas aparte del diseño actual: `templates/secciones/` (con su propio `secciones.css`, que usa los colores de `templates/estilo.css`).

## Agregar o quitar una fuente

Todas las fuentes están en `config/fuentes.json`. Se puede editar desde GitHub mismo (abra el archivo y pulse el lápiz ✏️).

**Para agregar una fuente**, copie un bloque existente, péguelo dentro de la lista (cuidando las comas) y cambie:

- `nombre`: cómo aparecerá en la página.
- `url`: la dirección del RSS. Para encontrarla, pruebe agregando `/feed/` o `/rss/` al final del sitio. Si no hay RSS, ponga la página de noticias.
- `tipo`: `rss` si es un RSS, `html` si es una página de noticias.
- `idioma`: `es`, `en` o `pt`.
- `region`: `internacional`, `latam`, `argentina`, `espana` u otra.
- `clase`: `oficial` (organizador o circuito), `medio` (medio especializado) o `comunidad` (Reddit, foros). Lo que solo informa una fuente `comunidad` nunca se publica como CONFIRMADO.
- Opcionales: `max_titulares` (tope propio por corrida, en lugar de los 10 generales); `uso`: `descubrimiento` para una fuente de fuentes como Reddit (aporta pocas notas, también en temas calientes, y los sitios que comparten sus usuarios suman como menciones para las fuentes candidatas); `tematica`: `juego` para medios de juego en general (como GamblingNews), de los que solo entran las notas que hablan de poker.
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

## Temas calientes

Un **tema caliente** es una historia que se sigue de cerca, por ejemplo el caso Paul Gregg. Las notas que mencionan una de sus palabras clave entran siempre, van primero y no cuentan para los topes ni para los filtros. Cada día, todas sus novedades aparecen juntas en una sola tarjeta con la etiqueta **Tema caliente**. Arriba de la página, la sección **Temas en vigilancia** muestra los temas activos.

Hay dos clases de temas:
- **Manuales:** los elige usted, en `config/temas_calientes.json`.
- **Automáticos:** el programa los crea solo cuando un mismo hecho aparece en 3 o más medios distintos en los últimos 3 días. Se apagan solos tras 7 días sin novedades y se guardan en `data/temas_auto.json`.

### Agregar, desactivar o quitar un tema con el botón (recomendado)

1. En GitHub, abra la pestaña **Actions** del repositorio.
2. A la izquierda, elija **Gestionar tema caliente**.
3. Pulse el botón **Run workflow**. Se abre un pequeño formulario:
   - **Acción:** elija `agregar`, `desactivar` o `quitar`.
   - **Nombre del tema:** por ejemplo `Caso Paul Gregg`.
   - **Palabras clave:** solo para agregar, separadas por coma. Por ejemplo: `Paul Gregg, MeshAgent, superuser`.
4. Pulse el botón verde **Run workflow**.
5. En uno o dos minutos el tema queda guardado y la página se actualiza.

Algunos detalles:
- Si agrega un tema que ya existe, se suman las palabras nuevas y el tema vuelve a quedar activo.
- **Desactivar** deja el tema guardado pero sin efecto; **quitar** lo borra.
- Si algo falla (por ejemplo, falta el nombre), el registro de esa ejecución dice el motivo.

### Editarlo a mano en la web de GitHub (alternativa)

1. Abra el archivo `config/temas_calientes.json` y pulse el lápiz ✏️ (Edit).
2. Cada tema es un bloque como este:
   ```json
   {
     "nombre": "Caso Paul Gregg (malware MeshAgent)",
     "palabras_clave": ["Paul Gregg", "MeshAgent", "superuser"],
     "alta": "2026-10-06",
     "estado": "activo"
   }
   ```
3. Para agregar un tema, copie un bloque, péguelo después del último (separado por una coma) y cambie los datos. Para desactivarlo, cambie `"activo"` por `"inactivo"`. Para quitarlo, borre el bloque completo y la coma sobrante.
4. Pulse **Commit changes** (abajo o arriba a la derecha) y confirme.
5. La sección **Temas en vigilancia** se actualiza en la próxima corrida. Si lo quiere ver antes, use el botón del punto anterior con la acción `agregar` y el mismo nombre.

## Avances de "día 1"

Las notas de avance de día 1 ("X lidera el día 1", "Day 1A", "chip leader tras el día 1") se descartan, salvo que sean de un torneo de `config/torneos_relevantes.json`. Ese archivo trae dos grupos:
- **Internacionales:** WSOP en todas sus variantes, EPT, WPT, Triton, WCOOP, SCOOP y PokerGO Tour.
- **Regionales:** CAP Circuito Argentino de Poker, LAPT y BSOP.

Para sumar un torneo, edite el archivo con el lápiz ✏️, copie un bloque y cambie el nombre y las claves (las palabras que aparecen en los titulares). El día 2 en adelante, las mesas finales, los resultados y los ganadores pasan siempre. Cada descarte queda anotado en el archivo del día, con el torneo y el motivo.

## Regla máxima: nunca inventar datos

Toda cifra, nombre, cargo, edad, nacionalidad, fecha, lugar, cita o hecho de una tarjeta debe estar escrito en el texto que se envió al modelo para esa tarjeta. El programa lo comprueba oración por oración:
- Lo que no puede rastrear, lo quita.
- Si falla el título o el "qué cambió", o si el resumen queda vacío, descarta la tarjeta.

Todo queda anotado en el campo `controles` del archivo de cada día. Para auditar, en `data/entradas/AAAA-MM-DD.json` se guarda, por nota, lo que se envió al modelo: fuente, dirección, titular y primera frase (hasta 200 caracteres).

## Cómo editar el diseño

El diseño está separado de la recolección: cambiar colores o letras nunca afecta a las noticias ni al historial. Todo se puede hacer desde GitHub, sin instalar nada: abra el archivo, pulse el lápiz ✏️, cambie el valor y pulse **Commit changes**.

Los colores se escriben como códigos hexadecimales, por ejemplo `#a8792f`. Para elegir uno, busque "selector de color" en internet, elija el tono y copie el código que empieza con `#`.

### Página actual (la que se publica todos los días)

- **Archivo que se edita:** `templates/estilo.css`. Todas las variables de color están en las primeras líneas, dentro de `:root`.
- **Modo oscuro:** más abajo, en el mismo archivo, los colores se repiten para el modo oscuro, en los dos bloques que empiezan con `@media (prefers-color-scheme: dark)` y `:root[data-theme="dark"]`. Si cambia un color y quiere que también cambie en modo oscuro, cámbielo en esos bloques.

| Variable | Qué colorea |
|---|---|
| `--bg` | Fondo de toda la página |
| `--surface` | Fondo de cada tarjeta |
| `--surface-2` | Fondo de la leyenda, de los títulos de cada día y de la lista de fuentes |
| `--ink` | Texto principal y titulares |
| `--ink-dim` | Textos secundarios: resúmenes, fechas, pie |
| `--line` | Bordes y líneas separadoras |
| `--accent` | Detalles de acento |
| `--accent-ink` | Enlaces "Leer nota", flechas y antetítulo |
| `--ok-bg`, `--ok-ink`, `--ok-dot` | Etiqueta CONFIRMADO: fondo, texto y punto |
| `--discuss-bg`, `--discuss-ink`, `--discuss-dot` | Etiqueta EN DISCUSIÓN: fondo, texto y punto |
| `--rumor-bg`, `--rumor-ink`, `--rumor-dot` | Etiqueta RUMOR: fondo, texto y punto |
| `--font-display` | Letra de los títulos |
| `--font-body` | Letra del texto |

**Cómo ver el cambio:** la página se vuelve a generar con cada corrida. Puede esperar a la corrida del día siguiente, o correr el flujo a mano (pestaña **Actions** → **Barrido diario** → **Run workflow**). Correrlo a mano también hace una consulta al modelo, de unos US$ 0,02.

Si quiere verlo al instante, haga el mismo cambio también en `docs/estilo.css`; GitHub Pages lo publica en 1 o 2 minutos. Ese archivo se reemplaza en cada corrida por una copia de `templates/estilo.css`, así que el cambio debe estar siempre en `templates/estilo.css`.

### Propuestas de diseño (pruebas)

- Propuesta A, editorial: `templates/propuestas/propuesta-a.css`. Se ve en https://hoozh.github.io/mesa-caliente/propuestas/propuesta-a.html
- Propuesta B, compacta: `templates/propuestas/propuesta-b.css`. Se ve en https://hoozh.github.io/mesa-caliente/propuestas/propuesta-b.html

Al principio de cada archivo hay un bloque `:root` donde cada variable tiene al lado una explicación de qué colorea. En ambas propuestas:

| Variable | Qué colorea |
|---|---|
| `--fondo` | Fondo de la página |
| `--superficie` | Fondo de las tarjetas o filas |
| `--tinta` | Texto principal y titulares |
| `--tinta-suave` | Fechas y resúmenes |
| `--acento` | Enlaces y botones |
| `--confirmado…`, `--discusion…`, `--rumor…` | Colores de cada clasificación |
| `--degradado-…` (A) o `--miniatura-…` (B) | Color que reemplaza a la imagen cuando no hay o no carga |
| `--latam…` | Sección "Escena argentina y latinoamericana" |

**Cómo ver el cambio:** las páginas de prueba usan una copia del CSS que está en `docs/propuestas/`. Para verlo al instante, haga el mismo cambio en `docs/propuestas/propuesta-a.css` (o `-b.css`); se publica en 1 o 2 minutos.

Quien tenga Python puede regenerar las propuestas con los datos del día. Esto copia los CSS de `templates/propuestas/` a `docs/propuestas/` y no toca la página oficial:

```bash
python scripts/probar_imagenes.py      # busca las imágenes de los últimos 2 días (no usa el modelo)
python scripts/generar_propuestas.py   # crea docs/propuestas/propuesta-a.html y propuesta-b.html
python scripts/verificar_propuestas.py # opcional (requiere Playwright): las abre en Chromium y revisa imágenes y filtros
```

**Consejo de legibilidad:** si cambia un color de texto o de fondo, compruebe el contraste con un verificador en línea ("contrast checker"). Debe dar al menos **4.5:1** para el texto normal.

## Cómo funciona por dentro

| Parte | Archivo |
|---|---|
| Fuentes y su salud | `config/fuentes.json` |
| Topes fijos y precios | `config/ajustes.json` |
| Un archivo por día | `data/dias/AAAA-MM-DD.json` |
| Direcciones ya vistas | `data/vistos.json` |
| Fuentes candidatas | `data/candidatas.json` |
| Tokens y costo por corrida (selección y redacción) | `data/costos.json` |
| Lo enviado al modelo, por nota (auditoría) | `data/entradas/AAAA-MM-DD.json` |
| Registro permanente y cambios en salas | `data/registro/hechos.jsonl` y `data/registro/cambios_salas.jsonl` |
| Salas vigiladas | `config/salas_vigiladas.json` |
| Brief semanal | `data/semanal/AAAA-Wnn.json` y `.md`, `docs/semanal/` |
| Plantillas de las secciones nuevas | `templates/secciones/` |
| Diseño de la página | `templates/index.html.j2` y `templates/estilo.css` |
| Página publicada | `docs/index.html` |

- **Salud de fuentes**: si una fuente falla 3 días seguidos (error, bloqueo, 403, sin contenido), pasa a `en_pausa`. Las pausadas se reintentan una vez por semana y vuelven a `activa` si responden. La página las lista en "Salud de fuentes" con el motivo.
- **Filtro por día**: cada corrida toma lo publicado desde la última corrida exitosa, con 3 horas de margen (`margen_horas`), y nunca mira más de 3 días hacia atrás (`dias_maximos_atras`). Si una fuente no trae fecha, o pone la misma a todas sus notas (como Poker.org), se usa el orden de la lista: se toma desde arriba hasta la primera nota ya vista. Una fuente nueva aporta como máximo 10 notas. Después de cada corrida exitosa, todo lo recogido (elegido o no) queda como visto; si la corrida falla, no se marca nada y la siguiente lo vuelve a intentar.
- **Topes fijos** (en `config/ajustes.json`): 10 titulares por fuente (Reddit, 3), 120 de la ventana más 40 pendientes en la selección, 20 artículos abiertos como máximo, 5500 tokens de salida en la redacción, 14 tarjetas generales y 6 latinas. El pie de la página muestra cuántos titulares se descartaron en la última corrida por fecha, por orden de la lista y por tope, con el detalle por fuente.
- **Artículos abiertos**: se abre el artículo de cada hecho elegido en la selección (y de las notas de temas calientes con titular insuficiente), como máximo 20 por corrida.
- **Candidatas**: el programa cuenta los dominios enlazados dentro de los artículos que abre y los que comparten los usuarios de las fuentes de descubrimiento (Reddit). Si un dominio aparece en al menos 3 artículos distintos en 14 días, no está en las fuentes y no es una red social, se agrega a "Fuentes candidatas". Nunca se agrega solo a la lista de fuentes y nunca se borra solo.
- **Robustez**: una corrida solo escribe el archivo de su propio día; nunca modifica días anteriores. Si el modelo falla, el día queda como "Corrida fallida" con el motivo y los titulares se vuelven a intentar al día siguiente.
- **Controles sobre el resumen**: el programa revisa lo que escribe el modelo. Conserva el premio que nombra la fuente (brazalete para la WSOP, anillo para el WSOP Circuit, trofeo), agrega la nacionalidad cuando el titular o la primera línea la indican y, en la escena latina, marca "[nacionalidad a confirmar]" si nadie la indica. Todo nombre de persona debe figurar en lo enviado al modelo: si el modelo agrega un nombre de pila, apellido o alias que no está, el programa lo quita, y si el nombre completo no figura, descarta la tarjeta. Además, el modelo recibe los títulos de las tarjetas de los últimos 5 días (`dias_titulos_previos` en `config/ajustes.json`) y no repite un hecho salvo que haya un desarrollo nuevo; en ese caso la tarjeta muestra "Qué cambió". Las tarjetas ya publicadas ese mismo día llegan al modelo con su resumen, y una "qué cambió" que no agrega nada nuevo se descarta.
- **Reglas de calidad** (`mesa/calidad.py`):
  - CONFIRMADO exige al menos una fuente oficial o un medio especializado; lo que se apoya solo en Reddit o foros queda EN DISCUSIÓN. Si una fuente no da la fecha, cuenta la fecha de la barrida. La hora se muestra solo cuando la fuente la da.
  - Cargos, posiciones, edades y nacionalidades de personas solo si figuran en el texto enviado al modelo; si no, se quitan.
  - Se descartan las notas sin relación con el poker, las puramente promocionales (satélites, promociones, ofertas, anuncios de garantizados) y las que no traen un dato concreto (un resultado sin el nombre del ganador, por ejemplo). Los titulares vagos se abren primero para buscar ese dato.
  - Se quitan los superlativos copiados de la fuente ("el más prestigioso del mundo", "espectacular").
  - "[nacionalidad a confirmar]" solo aparece junto al nombre de una persona (no junto a un torneo o una marca con sigla, como "Triton ONE") y nunca si la tarjeta o su fuente ya dicen de dónde es (por ejemplo, "el español").
  - Títulos en español con la jerga del poker y los nombres propios en inglés (score, runner-up, chip leader, bounty, Purple Jacket, Main Event).
  - Un hecho publicado en la sección general no se repite en la escena latina, aunque venga de otra fuente.
  - Las cifras se comparan por valor: "US$ 800.000" coincide con "$800K", "US$ 1 millón" con "$1,000,000" y "270.000" con "270 mil". El año en curso agregado al nombre de una temporada ("Fall 2026") no cuenta como dato inventado. Las traducciones habituales ("Damas" por "Ladies", "Jugador del Año" por "Player of the Year", "Gales" por "Wales") y los nombres con apóstrofo o guion ("King's", "Mix-Max") tampoco. Las abreviaturas ("St. Louis") no cortan las oraciones.
  - Si una fuente latinoamericana solo pone "$", el monto se publica como "$ N (moneda a confirmar)".

  Lo que el programa corrige o descarta queda anotado en el archivo del día (campo `controles`).
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
