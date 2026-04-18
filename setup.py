from setuptools import setup, find_packages

setup(
    name="nexus-mcp-ci",
    version="1.0.1",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    install_requires=[
        "fastmcp>=2.0.0",
        "lancedb>=0.4.0",
        "sentence-transformers>=3.0.0",
        "optimum>=1.19.0",
        "onnxruntime>=1.17.0",
        "tree-sitter==0.21.3",
        "tree-sitter-languages>=1.10.0",
        "ast-grep-py>=0.28.0",
        "rustworkx>=0.15.0",
        "pathspec>=0.11.0",
        "watchdog>=3.0.0",
        "pyarrow>=14.0.0",
    ],
    entry_points={
        "console_scripts": [
            "nexus-mcp-ci=nexus_mcp.server:main",
        ],
    },
)
