import pathlib
import sys

from streamlit.web import cli


def main():
    script_path = pathlib.Path(__file__).parent / "app.py"
    cli.main_run([str(script_path)] + sys.argv[1:])


if __name__ == "__main__":
    main()
