# Licensed under the GPL: https://www.gnu.org/licenses/gpl-3.0.en.html
# For details: reprotest/debian/copyright

from setuptools import setup, find_packages

setup(name='reprotest',
      version='0.2',
      description='Build packages and check them for reproducibility.',
      long_description=open('README.md', encoding='utf-8').read(),
      author='Ceridwen',
      author_email='ceridwenv@gmail.com',
      license='GPL-3+',
      url='https://anonscm.debian.org/cgit/reproducible/reprotest.git/',
      packages=find_packages(),
      entry_points={
          'console_scripts': [
              'reprotest = reprotest:main'
              ],
          },
      install_requires=[
          'diffoscope',
          ],
      classifiers=[
          'Development Status :: 3 - Alpha',
          'Intended Audience :: Developers',
          'License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)',
          'Operating System :: POSIX',
          'Programming Language :: Python',
          'Programming Language :: Python :: 3',
          'Programming Language :: Python :: 3.5',
          'Topic :: Utilities',
          ],
      zip_safe=False,
      include_package_data=True
      )
