from setuptools import setup, find_packages

all_packages = find_packages(".")

setup(
    name="picobot",
    version="0.2.0",
    author="Picobot Contributors",
    description="A lightweight AI agent framework",
    long_description=open("README.md").read() if open("README.md", "r").readable() else "",
    long_description_content_type="text/markdown",
    url="https://github.com/HKUDS/picobot",
    package_dir={"picobot": "."},
    packages=["picobot"] + ["picobot." + p for p in all_packages],
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
    python_requires=">=3.8",
    install_requires=[
        "typer>=0.9.0",
        "rich>=13.0.0",
        "prompt-toolkit>=3.0.0",
        "loguru>=0.7.0",
        "httpx>=0.24.0",
        "oauth-cli-kit>=0.1.0",
        "litellm>=1.0.0",
        "pydantic-settings>=2.0.0",
        "json_repair>=0.1.0",
    ],
    entry_points={
        "console_scripts": [
            "picobot=picobot.__main__:app",
        ],
    },
)
