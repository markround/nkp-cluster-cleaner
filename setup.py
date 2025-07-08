from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

with open("requirements.txt", "r", encoding="utf-8") as fh:
    requirements = [line.strip() for line in fh if line.strip() and not line.startswith("#")]

setup(
    name="nkp-cluster-cleaner",
    version="0.2.0",
    author="Mark Dastmalchi-Round",
    author_email="github@markround.com",
    description="A tool to delete CAPI-provided Kubernetes clusters based on label criteria",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/markround/nkp-cluster-cleaner",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: System Administrators",
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
    install_requires=requirements,
    entry_points={
        "console_scripts": [
            "nkp-cluster-cleaner=nkp_cluster_cleaner.main:cli",
        ],
    },
)