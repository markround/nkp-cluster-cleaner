from setuptools import setup, find_packages
import re

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

with open("requirements.txt", "r", encoding="utf-8") as fh:
    requirements = [
        line.strip() for line in fh if line.strip() and not line.startswith("#")
    ]


# Get values from __init__.py so we can cut down on re-declaring stuff
def get_property(prop, project):
    result = re.search(
        r'{}\s*=\s*[\'"]([^\'"]*)[\'"]'.format(prop),
        open("src/" + project + "/__init__.py").read(),
    )
    return result.group(1)


project_name = "nkp_cluster_cleaner"

setup(
    name="nkp-cluster-cleaner",
    version=get_property("__version__", project_name),
    author=get_property("__author__", project_name),
    author_email=get_property("__email__", project_name),
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
