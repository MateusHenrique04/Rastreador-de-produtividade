# -*- coding: utf-8 -*-
"""
reparar_db.py — recupera um tracker.db corrompido ("database disk image is malformed")
-------------------------------------------------------------------------------------

Seguro por padrão: NADA do seu banco original é alterado ou apagado.

O que ele faz:
  1) Copia tracker.db (+ tracker.db-wal e tracker.db-shm, se existirem) para uma
     pasta de backup  backup_tracker_AAAAMMDD_HHMMSS/  e trabalha só nas cópias.
  2) Roda o diagnóstico (PRAGMA integrity_check).
  3) Cria um banco novo (tracker_reparado.db) e copia dele tudo o que ainda é
     legível, tabela por tabela, em blocos; blocos com páginas danificadas são
     subdivididos até isolar só as linhas realmente perdidas.
  4) Confere o banco novo e, se você confirmar, troca o tracker.db pelo reparado
     (o antigo vai para a pasta de backup).

ANTES DE RODAR: feche o tracker.py e o dashboard_live.py.

Uso:
    python reparar_db.py              (pergunta antes de trocar o banco)
    python reparar_db.py --yes        (troca sem perguntar)
    python reparar_db.py --db caminho\\tracker.db
"""
import argparse
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CHUNK = 5000

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL,
        app TEXT NOT NULL,
        context TEXT NOT NULL,
        timestamp DATETIME NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS afk_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at DATETIME NOT NULL,
        ended_at DATETIME,
        duration_seconds REAL)""",
]
INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_afk_started ON afk_sessions(started_at)",
]
TABLES = {
    "logs": ["id", "type", "app", "context", "timestamp"],
    "afk_sessions": ["id", "started_at", "ended_at", "duration_seconds"],
}


def integrity(path, limit=10):
    try:
        c = sqlite3.connect(path)
        try:
            return [r[0] for r in c.execute(f"PRAGMA integrity_check({limit})")]
        finally:
            c.close()
    except sqlite3.Error as e:
        return [f"erro: {e}"]


def is_ok(msgs):
    return msgs == ["ok"]


def copy_with_sidecars(db, dest_dir):
    os.makedirs(dest_dir, exist_ok=True)
    out = os.path.join(dest_dir, os.path.basename(db))
    for ext in ("", "-wal", "-shm"):
        if os.path.exists(db + ext):
            shutil.copy2(db + ext, out + ext)
    return out


def scan_last_id(src, table):
    last = 0
    try:
        for (i,) in src.execute(f"SELECT id FROM {table}"):
            last = max(last, i)
    except sqlite3.Error:
        pass
    return last


def max_id(src, table):
    try:
        return src.execute(f"SELECT MAX(id) FROM {table}").fetchone()[0] or 0
    except sqlite3.Error:
        return scan_last_id(src, table)


def salvage_table(src, dst, table, cols):
    col_list = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    insert = f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES ({placeholders})"
    state = {"copied": 0, "lost": 0}

    def copy_range(lo, hi):  # (lo, hi]
        try:
            rows = src.execute(
                f"SELECT {col_list} FROM {table} WHERE id > ? AND id <= ?", (lo, hi)
            ).fetchall()
        except sqlite3.Error:
            if hi - lo <= 1:
                state["lost"] += 1
                return
            mid = (lo + hi) // 2
            copy_range(lo, mid)
            copy_range(mid, hi)
            return
        if rows:
            dst.executemany(insert, rows)
            state["copied"] += len(rows)

    top = max_id(src, table)
    started = time.time()
    for lo in range(0, top, CHUNK):
        copy_range(lo, min(lo + CHUNK, top))
        if (lo // CHUNK) % 40 == 0:
            print(f"  {table}: {state['copied']:,} linhas recuperadas "
                  f"({min(lo + CHUNK, top) * 100 // max(top, 1)}%)", end="\r", flush=True)
    dst.commit()
    print(f"  {table}: {state['copied']:,} linhas recuperadas, "
          f"{state['lost']:,} perdidas ({time.time() - started:.1f}s)      ")
    return state["copied"], state["lost"]


def build_repaired(work_db, out_db):
    if os.path.exists(out_db):
        os.remove(out_db)
    src = sqlite3.connect(work_db)
    src.text_factory = lambda b: b.decode("utf-8", "replace")  # texto inválido vira "?" em vez de erro
    dst = sqlite3.connect(out_db)
    for stmt in SCHEMA:
        dst.execute(stmt)
    totals = {}
    for table, cols in TABLES.items():
        try:
            totals[table] = salvage_table(src, dst, table, cols)
        except sqlite3.Error as e:
            print(f"  {table}: não foi possível ler esta tabela ({e})")
            totals[table] = (0, -1)
    for stmt in INDEXES:
        dst.execute(stmt)
    dst.commit()
    src.close()
    dst.close()
    return totals


def main():
    ap = argparse.ArgumentParser(description="Repara o tracker.db")
    ap.add_argument("--db", default=os.path.join(BASE_DIR, "tracker.db"))
    ap.add_argument("--yes", action="store_true", help="trocar o banco sem perguntar")
    args = ap.parse_args()
    db = os.path.abspath(args.db)

    if not os.path.exists(db):
        print(f"Não encontrei {db}")
        sys.exit(1)

    if not args.yes:
        input("Feche o tracker.py e o dashboard_live.py e pressione Enter para continuar...")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(os.path.dirname(db), f"backup_tracker_{stamp}")
    original_copy = copy_with_sidecars(db, os.path.join(backup_dir, "original"))
    print(f"Backup feito em: {backup_dir}\\original")
    sidecars = [e for e in ("-wal", "-shm") if os.path.exists(db + e)]
    print(f"Arquivos auxiliares encontrados: {', '.join(sidecars) if sidecars else 'nenhum'}")

    print("\nDiagnóstico (cópia completa):")
    msgs = integrity(original_copy)
    for m in msgs[:10]:
        print("  ", m)
    if is_ok(msgs):
        print("\nO banco está íntegro. O erro pode ter sido momentâneo (arquivo em uso/sincronizando).")
        print("Tente abrir o dashboard de novo. Nada foi alterado.")
        return

    # Variante só com o arquivo principal (caso o .db-wal esteja inconsistente)
    candidates = [("completo (com -wal)", original_copy)]
    if sidecars:
        main_only_dir = os.path.join(backup_dir, "so_principal")
        os.makedirs(main_only_dir, exist_ok=True)
        main_only = os.path.join(main_only_dir, os.path.basename(db))
        shutil.copy2(db, main_only)
        print("\nDiagnóstico (somente o arquivo principal, sem -wal):")
        m2 = integrity(main_only)
        for m in m2[:5]:
            print("  ", m)
        candidates.append(("somente principal", main_only))

    repaired = os.path.join(os.path.dirname(db), "tracker_reparado.db")
    results = []
    for i, (label, path) in enumerate(candidates):
        print(f"\nRecuperando a partir de: {label}")
        out = f"{repaired}.{i}.tmp"
        totals = build_repaired(path, out)
        got = sum(t[0] for t in totals.values())
        print(f"  total recuperado: {got:,} linhas")
        results.append((got, out, label, totals))
    got, out, label, totals = max(results, key=lambda r: r[0])
    if os.path.exists(repaired):
        os.remove(repaired)
    os.replace(out, repaired)
    for _, other, _, _ in results:
        if other != out and os.path.exists(other):
            os.remove(other)

    print(f"\nEscolhida: {label} ({got:,} linhas). Conferindo o banco novo...")
    msgs = integrity(repaired)
    print("  integrity_check:", ", ".join(msgs[:3]))
    if not is_ok(msgs):
        print("O banco reparado ainda tem problemas; não vou trocar nada. Guarde a pasta de backup.")
        sys.exit(2)

    lost = sum(max(t[1], 0) for t in totals.values())
    print(f"Linhas perdidas (irrecuperáveis): {lost:,}")

    if not args.yes:
        ans = input("\nSubstituir o tracker.db pelo reparado? O antigo fica no backup. (s/n) ").strip().lower()
        if ans not in ("s", "sim", "y", "yes"):
            print(f"Ok. O banco reparado ficou em: {repaired}")
            return

    old_dir = os.path.join(backup_dir, "antigo_substituido")
    os.makedirs(old_dir, exist_ok=True)
    for ext in ("", "-wal", "-shm"):
        if os.path.exists(db + ext):
            shutil.move(db + ext, os.path.join(old_dir, os.path.basename(db) + ext))
    shutil.move(repaired, db)
    cache = os.path.join(os.path.dirname(db), "dashboard_cache.db")
    if os.path.exists(cache):
        os.remove(cache)  # será refeito pelo dashboard (os dados do cache vieram do banco antigo)
        print("dashboard_cache.db removido (o dashboard refaz sozinho).")
    print("\nPronto! tracker.db substituído. Pode abrir o tracker.py e o dashboard_live.py.")


if __name__ == "__main__":
    main()
