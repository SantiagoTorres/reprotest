# Licensed under the GPL: https://www.gnu.org/licenses/gpl-3.0.en.html
# For details: reprotest/debian/copyright

import os

with open('tests/irreproducible_artifact', 'wb') as irreproducible_artifact:
    irreproducible_artifact.write(os.urandom(1024))
