import os

from remip_example import app
from remip_example.utils import ensure_node

if __name__ == "__main__":
    NODE_BIN_DIR = ensure_node()
    if str(NODE_BIN_DIR) not in os.environ["PATH"]:
        os.environ["PATH"] = os.pathsep.join(
            (str(NODE_BIN_DIR), os.environ.get("PATH", ""))
        )

    app.main()
