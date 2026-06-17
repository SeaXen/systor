"""systor — lightweight Linux system monitor with web dashboard & alerts."""
from setuptools import setup, find_packages

setup(
    name="systor",
    version="0.1.0",
    description="Lightweight Linux system monitor with web dashboard, sustained-threshold alerts, and Telegram/Discord notifications.",
    long_description=open("README.md").read() if __import__("os").path.exists("README.md") else "",
    long_description_content_type="text/markdown",
    author="Dr. Sagar (SeaXen)",
    license="MIT",
    python_requires=">=3.9",
    packages=find_packages(exclude=["tests", "tests.*"]),
    include_package_data=True,
    package_data={
        "systor": ["templates/*.html", "static/css/*.css", "static/js/*.js"],
    },
    entry_points={
        "console_scripts": [
            "systor=systor.cli:main",
        ],
    },
    install_requires=[
        "Flask>=3.0",
        "waitress>=3.0",
    ],
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Web Environment",
        "Intended Audience :: System Administrators",
        "License :: OSI Approved :: MIT License",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3",
        "Topic :: System :: Monitoring",
    ],
    url="https://github.com/SeaXen/systor",
)
