"""Sample: Edit design/prompt/themes/catalog.json. Run this tool. The digest field changes to the new hash."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from durai.theme_intent import CATALOG_PATH, catalog_digest  # noqa: E402


def main():
    # Sample: Read the catalog. Write the digest of its body into the digest field. Print the digest.
    catalog = json.loads(CATALOG_PATH.read_text(encoding='utf-8'))
    catalog['digest'] = catalog_digest(catalog)
    CATALOG_PATH.write_text(json.dumps(catalog, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    print(catalog['digest'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
