from setuptools import setup

setup(name='dq',
      version='0.1.0',
      description='dead-simple download queue manager',
      author='Adrian Sampson',
      author_email='adrian@radbox.org',
      url='https://github.com/sampsyo/dq',
      license='MIT',
      platforms='ALL',
      # long_description=_read('README.rst'),

      install_requires=['pyyaml'],

      py_modules=['dq'],

      entry_points={
          'console_scripts': [
              'dq = dq:main',
          ],
      },

      classifiers=[
          'Environment :: Console',
          'Intended Audience :: End Users/Desktop',
          'Programming Language :: Python :: 2',
          'Topic :: Utilities',
      ],
)
