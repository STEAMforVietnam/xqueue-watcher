#!/usr/bin/env python

from __future__ import print_function
import sys
import time
import json
import signal
import inspect
import logging
import importlib
from path import path
import logging.config

from .sandbox import Sandbox


class Manager(object):
    """
    Manages polling connections to XQueue.
    """
    def __init__(self):
        self.clients = []
        self.poll_time = 10
        self.log = logging

    def client_from_config(self, queue_name, config):
        """
        Return an XQueueClient from the configuration object.
        """
        from . import client

        klass = getattr(client, config.get('CLASS', 'XQueueClientThread'))
        watcher = klass(queue_name,
                       xqueue_server=config.get('SERVER', 'http://localhost:18040'),
                       auth=config.get('AUTH', (None, None)))

        for handler_config in config.get('HANDLERS', []):
            handler_name = handler_config['HANDLER']
            mod_name, classname = handler_name.rsplit('.', 1)
            module = importlib.import_module(mod_name)

            kw = handler_config.get('KWARGS', {})

            # HACK
            # Graders should use codejail instead of this other sandbox implementation
            sandbox_config = handler_config.get('SANDBOX')
            if sandbox_config:
                kw['sandbox'] = Sandbox(logging.getLogger('xqueuewatcher.sandbox.{}'.format(queue_name)),
                                        python_path=sandbox_config,
                                        do_sandboxing=True)
            handler = getattr(module, classname)
            if kw or inspect.isclass(handler):
                # handler could be a function or a class
                handler = handler(**kw)
            watcher.add_handler(handler)
        return watcher

    def configure(self, configuration):
        """
        Configure XQueue clients.
        """
        for queue_name, config in configuration.items():
            for i in range(config.get('CONNECTIONS', 1)):
                watcher = self.client_from_config(queue_name, config)
                self.clients.append(watcher)

    def configure_from_directory(self, directory):
        """
        Load configuration files from a directory
        """
        directory = path(directory)
        log_config = directory / 'logging.json'
        if log_config.exists():
            logging.config.dictConfig(json.load(log_config.open()))
        else:
            logging.basicConfig(level="DEBUG")
        self.log = logging.getLogger('xqueue_watcher.manager')

        codejail_config = directory / 'codejail.json'
        if codejail_config.exists():
            self.enable_codejail(json.load(codejail_config.open()))

        for watcher in directory.files('*.json'):
            if watcher.basename() in ('logging.json', 'codejail.json'):
                continue
            self.configure(json.load(watcher.open()))

    def enable_codejail(self, codejail_config):
        """
        Enable codejail for the process.
        codejail_config is a dict like this:
        {
            "python": {
                "python_bin": "/path/to/python",
                "user": "sandbox_username",
                "limits": {
                    "CPU": 1,
                    ...
                }
            },
            "other-python": {
                "python_bin": "/path/to/other/python"
            }
        }
        limits are optional
        user defaults to the current user
        """
        import codejail.jail_code
        import getpass
        for cj_name, config in codejail_config.items():
            python_bin = config.get('python_bin')
            if python_bin:
                user = config.get('user', getpass.getuser())
                codejail.jail_code.configure(cj_name, python_bin, user=user)
                limits = config.get("limits", {})
                for name, value in limits.items():
                    codejail.jail_code.set_limit(name, value)
                self.log.info("configured codejail -> %s %s %s", cj_name, python_bin, user)

    def start(self):
        """
        Start XQueue client threads (or processes).
        """
        for c in self.clients:
            self.log.info('Starting %r', c)
            c.start()

    def wait(self):
        """
        Monitor clients.
        """
        if not self.clients:
            return
        signal.signal(signal.SIGTERM, self.shutdown)
        while 1:
            for client in self.clients:
                if not client.is_alive():
                    self.log.error('Client died -> %r',
                                   client.queue_name)
                    self.shutdown()
                try:
                    time.sleep(self.poll_time)
                except KeyboardInterrupt:  # pragma: no cover
                    self.shutdown()

    def shutdown(self, *args):
        """
        Cleanly shutdown all clients.
        """
        self.log.info('shutting down')
        while self.clients:
            client = self.clients.pop()
            client.shutdown()
            if client.processing:
                try:
                    client.join()
                except RuntimeError:
                    self.log.exception("joining")
                    sys.exit(9)
            self.log.info('%r done', client)
        self.log.info('done')
        sys.exit()


def main(args=None):
    import argparse
    parser = argparse.ArgumentParser(prog="xqueue_watcher", description="Run grader from settings")
    parser.add_argument('-d', '--confd', required=True, help='load configuration from directory')

    args = parser.parse_args(args)

    manager = Manager()
    manager.configure_from_directory(args.confd)

    if not manager.clients:
        print("No xqueue watchers configured")
    manager.start()
    manager.wait()
    return 0
