"""
corrigir_banco.py
-----------------
Corrige logs com app='Outros' gravados antes das regras do classifier
existirem. Usa o classifier atual para reclassificar pelo context.

Uso:
    python corrigir_banco.py            → preview
    python corrigir_banco.py --apply    → aplica
"""

import sqlite3
import sys
import argparse
from collections import Counter

sys.path.insert(0, ".")
from classifier import split_app_context

DB_NAME = "tracker.db"


def main(apply: bool):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("SELECT id, context FROM logs WHERE app = 'Outros'")
    rows = c.fetchall()

    updates = []
    counter = Counter()
    for id_, context in rows:
        new_app, _ = split_app_context(context)
        if new_app != "Outros":
            updates.append((new_app, id_))
            counter[new_app] += 1

    print(f"\n{'='*50}")
    print(f"  Total com app='Outros' : {len(rows)}")
    print(f"  Serao corrigidos       : {len(updates)}")
    print(f"  Permanecem Outros      : {len(rows) - len(updates)}")
    print(f"\n  Distribuicao:")
    for app, cnt in counter.most_common():
        print(f"    {app:30} -> {cnt}")

    if not apply:
        print(f"\n  PREVIEW - nenhuma alteracao feita.")
        print(f"  Para aplicar: python corrigir_banco.py --apply\n")
    else:
        if updates:
            c.executemany("UPDATE logs SET app = ? WHERE id = ?", updates)
            conn.commit()
            print(f"\n  {len(updates)} logs corrigidos.\n")
        else:
            print(f"\n  Nada a corrigir.\n")

    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    main(apply=args.apply)