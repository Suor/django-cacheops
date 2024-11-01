from setuptools import setup


# Remove build status
README = open('README.rst').read().replace('|Build Status|', '', 1)


setup(
    name='django-cacheops',
    version='7.1',
    author='Alexander Schepanovski',
    author_email='suor.web@gmail.com',

    description='A slick ORM cache with automatic granular event-driven invalidation for Django.',
    long_description=README,
    url='http://github.com/Suor/django-cacheops',
    license='BSD',

    packages=[
        'cacheops',
        'cacheops.management',
        'cacheops.management.commands',
        'cacheops.templatetags'
    ],
    python_requires='>=3.7',
    install_requires=[
        'django>=3.2',
        'redis>=3.0.0',
        'funcy>=1.8',
    ],
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'License :: OSI Approved :: BSD License',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Framework :: Django',
        'Framework :: Django :: 3.2',
        'Framework :: Django :: 4.0',
        'Framework :: Django :: 4.1',
        'Framework :: Django :: 4.2',
        'Framework :: Django :: 5.0',

        'Environment :: Web Environment',
        'Intended Audience :: Developers',
        'Topic :: Internet :: WWW/HTTP',
        'Topic :: Software Development :: Libraries :: Python Modules',
    ],

    zip_safe=False,
    include_package_data=True,
)
