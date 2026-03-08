from pathlib import Path

for line in Path("input.tsv").read_text().splitlines():
    if line.startswith("tscalf3o\t"):
        print(line)
        print(repr(line))
