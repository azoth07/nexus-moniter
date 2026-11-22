import argparse
import ast
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="从 Python 客户端导入用户设定")
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    settings = {}
    for statement in ast.parse(args.source.read_text(encoding="utf-8-sig")).body:
        if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Constant):
            for target in statement.targets:
                if isinstance(target, ast.Name) and target.id in ("SERVER_URL", "SERVER_KEY"):
                    settings[target.id] = statement.value.value
    if len(settings) != 2 or not all(isinstance(value, str) for value in settings.values()):
        parser.error("源文件需要包含 SERVER_URL 和 SERVER_KEY 两个字符串常量")
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(settings, output, ensure_ascii=False, indent=2)
        output.write("\n")


if __name__ == "__main__":
    main()
