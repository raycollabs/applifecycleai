"""Sample: Edit design/prompt/compare/template.json. Run this tool. The digest field changes to the new hash."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from durai.compare_intent import TEMPLATE_PATH, template_digest  # noqa: E402


def main():
    # Sample: Read the template. Write the digest of its body into the digest field. Print the digest.
    template = json.loads(TEMPLATE_PATH.read_text(encoding='utf-8'))
    template['digest'] = template_digest(template)
    TEMPLATE_PATH.write_text(json.dumps(template, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    print(template['digest'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
