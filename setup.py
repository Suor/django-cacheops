from setuptools import setup

setup(
    name='django-cacheops',
    version='1.2.1',
    author='Alexander Schepanovski',
    author_email='suor.web@gmail.com',

    description='A slick ORM cache with automatic granular event-driven invalidation for Django.',
    long_description=open('README.rst').read(),
    url='http://github.com/Suor/django-cacheops',
    license='BSD',

    packages=[
        'cacheops',
        'cacheops.management',
        'cacheops.management.commands',
        'cacheops.templatetags'
    ],
    install_requires=[
        'django>=1.2',
        'redis>=2.4.12',
        'simplejson>=2.2.0',
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

        'Framework :: Django',
        'Environment :: Web Environment',
        'Intended Audience :: Developers',
        'Topic :: Internet :: WWW/HTTP',
        'Topic :: Software Development :: Libraries :: Python Modules',
    ]
)
