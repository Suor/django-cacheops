from setuptools import setup


# Remove build status and move Gitter link under title for PyPi
README = open('README.rst').read()    \
    .replace('|Build Status|', '', 1) \
    .replace('|Gitter|', '', 1)       \
    .replace('===\n', '===\n\n|Gitter|\n')


setup(
    name='django-cacheops',
    version='3.0.1',
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
    install_requires=[
        'django>=1.7',
        'redis>=2.9.1',
        'funcy>=1.2,<2.0',
        'six>=1.4.0',
    ],

    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'License :: OSI Approved :: BSD License',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.3',
        'Programming Language :: Python :: 3.4',
        'Programming Language :: Python :: 3.5',
        'Framework :: Django',
        'Framework :: Django :: 1.7',
        'Framework :: Django :: 1.8',
        'Framework :: Django :: 1.9',
        'Framework :: Django :: 1.10',

        'Environment :: Web Environment',
        'Intended Audience :: Developers',
        'Topic :: Internet :: WWW/HTTP',
        'Topic :: Software Development :: Libraries :: Python Modules',
    ],

    zip_safe=False,
    include_package_data=True,
)
