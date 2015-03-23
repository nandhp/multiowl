import logging
from . import gtkinterface
from .__init__ import MultiowlApp

logging.basicConfig(level=logging.DEBUG,
                    datefmt='%Y-%m-%d %H:%M:%S',
                    format="[%(asctime)s] %(threadName)s <%(name)s> %(message)s")
MultiowlApp(gtkinterface)
