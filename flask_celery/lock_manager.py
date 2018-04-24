import hashlib
from .exceptions import OtherInstanceError
from flask_celery.backends.database import LockBackendDb
from flask_celery.backends.redis import LockBackendRedis
from flask_celery.backends.filesystem import LockBackendFilesystem
from logging import getLogger

try:
    from urllib.parse import urlparse
except ImportError:
    from urlparse import urlparse


def select_lock_backend(task_lock_backend):
    parsed_backend_uri = urlparse(task_lock_backend)
    scheme = str(parsed_backend_uri.scheme)

    if scheme.startswith('redis'):
        lock_manager = LockBackendRedis
    elif scheme.startswith(('sqla+', 'db+', 'mysql', 'postgresql', 'sqlite')):
        lock_manager = LockBackendDb
    elif scheme.startswith('file'):
        lock_manager = LockBackendFilesystem
    else:
        raise NotImplementedError('No backend found for {}'.format(task_lock_backend))
    return lock_manager


class LockManager(object):
    """Base class for other lock managers."""

    def __init__(self, lock_backend, celery_self, timeout, include_args, args, kwargs):
        """May raise NotImplementedError if the Celery backend is not supported.

        :param celery_self: From wrapped() within single_instance(). It is the `self` object specified in a binded
            Celery task definition (implicit first argument of the Celery task when @celery.task(bind=True) is used).
        :param int timeout: Lock's timeout value in seconds.
        :param bool include_args: If single instance should take arguments into account.
        :param iter args: The task instance's args.
        :param dict kwargs: The task instance's kwargs.
        """
        self.lock_backend = lock_backend
        self.celery_self = celery_self
        self.timeout = timeout
        self.include_args = include_args
        self.args = args
        self.kwargs = kwargs
        self.log = getLogger('{0}:{1}'.format(self.__class__.__name__, self.task_identifier))

    @property
    def task_identifier(self):
        """Return the unique identifier (string) of a task instance."""
        task_id = self.celery_self.name
        if self.include_args:
            merged_args = str(self.args) + str([(k, self.kwargs[k]) for k in sorted(self.kwargs)])
            task_id += '.args.{0}'.format(hashlib.md5(merged_args.encode('utf-8')).hexdigest())
        return task_id

    def __enter__(self):
        self.log.debug('Timeout %ds | Key %s', self.timeout, self.task_identifier)
        if not self.lock_backend.acquire(self.task_identifier, self.timeout):
            self.log.debug('Another instance is running.')
            raise OtherInstanceError('Failed to acquire lock, {0} already running.'.format(self.task_identifier))
        else:
            self.log.debug('Got lock, running.')

    def __exit__(self, exc_type, *_):
        if exc_type == OtherInstanceError:
            # Failed to get lock last time, not releasing.
            return
        self.log.debug('Releasing lock.')
        self.lock_backend.release(self.task_identifier)

    @property
    def is_already_running(self):
        """Return True if lock exists and has not timed out."""
        return self.lock_backend.exists(self.task_identifier, self.timeout)

    def reset_lock(self):
        """Removed the lock regardless of timeout."""
        self.lock_backend.release(self.task_identifier)