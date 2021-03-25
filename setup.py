#!/usr/bin/env python

"""The setup script."""

from setuptools import setup, find_packages

with open('README.md') as readme_file:
    readme = readme_file.read()

requirements = [ ]

setup_requirements = [ ]

test_requirements = [ ]

setup(
    author="James Graham",
    author_email='james@hoppipolla.co.uk',
    python_requires='>=3.6',
    classifiers=[
        'Development Status :: 2 - Pre-Alpha',
        'Intended Audience :: Developers',
        'Natural Language :: English',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
    ],
    description="Figure out the number of tests of different types from a git checkout of mozilla-central.",
    entry_points={
        'console_scripts': [
            'mozteststat=mozteststat.main:run',
        ],
    },
    install_requires=requirements,
    long_description=readme,
    include_package_data=True,
    keywords='mozteststat',
    name='mozteststat',
    packages=find_packages(include=['mozteststat', 'mozteststat.*']),
    setup_requires=setup_requirements,
    test_suite='tests',
    tests_require=test_requirements,
    url='https://github.com/jgraham/mozteststat',
    version='0.1.0',
    zip_safe=False,
)
