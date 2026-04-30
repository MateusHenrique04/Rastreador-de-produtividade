# Dashboard ao vivo (local, gratis)

Versao do dashboard que se atualiza sozinho a cada 30 segundos, sem piscar
e sem precisar regenerar o HTML manualmente. Roda 100% local no seu PC.

## Como usar

1. Copie estes arquivos para a MESMA pasta onde estao
   `tracker.py`, `dashboard.py`, `classifier.py`, `rules.json` e `tracker.db`:
   - `dashboard_live.py`
   - a pasta `templates/` inteira (com `dashboard.html` dentro)

2. Instale o Flask (uma vez so):

   ```
   pip install flask
   ```

3. Rode o servidor:

   ```
   python dashboard_live.py
   ```

4. O navegador abre sozinho em `http://localhost:5000`. Pode deixar a aba
   aberta — ela vai puxar dados novos do `tracker.db` a cada 30 segundos.

## Como funciona

- O Flask serve duas rotas:
  - `GET /` -> entrega o HTML uma vez
  - `GET /api/data` -> le `tracker.db` e devolve tudo em JSON
- O JS no navegador chama `/api/data` a cada 30s e atualiza os graficos
  com `chart.update()` (animacao suave, sem reload da pagina)
- A logica de processamento e' a mesma do `dashboard.py` original

## Mudar o intervalo de atualizacao

No `templates/dashboard.html` procure por:

```
const REFRESH_MS = 30000;
```

Troque por `10000` (10s), `60000` (1min), etc.

## Parar o servidor

CTRL+C no terminal onde voce rodou `python dashboard_live.py`.
