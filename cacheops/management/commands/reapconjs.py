from argparse import ArgumentParser

from django.core.management.base import BaseCommand

from cacheops.reaper import reap_conjs


class Command(BaseCommand):
    help = 'Removes expired conjunction keys from cacheops.'

    def add_arguments(self, parser: ArgumentParser):
        parser.add_argument('--chunk-size', type=int, default=1000)
        parser.add_argument('--min-conj-set-size', type=int, default=1000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, chunk_size: int, min_conj_set_size: int, dry_run: bool, **kwargs):
        reap_conjs(
            chunk_size=chunk_size,
            min_conj_set_size=min_conj_set_size,
            dry_run=dry_run,
        )
