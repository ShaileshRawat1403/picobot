from setuptools import find_packages, setup

setup(
    name="picobot",
    version="1.0.0",
    author="Picobot Contributors",
    description="A lightweight, privacy-focused AI agent framework",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/picobot-ai/picobot",
    packages=find_packages(where="picobot"),
    package_dir={"": "picobot"},
    classifiers=[
        "Development Status :: 4 - Beta",
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
        "json-repair>=0.1.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0.0",
            "pytest-asyncio>=0.21.0",
            "ruff>=0.1.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "picobot=picobot.__main__:app",
        ],
    },
)
