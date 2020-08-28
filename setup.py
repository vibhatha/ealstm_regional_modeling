import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="ealstm_regional_modeling",  # Replace with your own username
    version="0.0.1",
    author="Kratzert, F., Klotz, D., Shalev, G., Klambauer, G., Hochreiter, S., and Nearing, G.",
    author_email="",
    description="EALSTM Packaging",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/kratzert/ealstm_regional_modeling",
    packages=setuptools.find_packages(),
    python_requires='>=3.6.8',
)
