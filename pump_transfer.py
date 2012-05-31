#!/usr/bin/env python

import copy
import logging
import optparse
import os
import sys
import threading

import pump
import pump_bfd
import pump_cb
import pump_mbf
import pump_mc
import pump_tap

from pump import PumpingStation

class Transfer:
    """Base class for 2.0 Backup/Restore/Transfer."""

    # TODO: (2) upgrade 1.8 files to 2.0 server files.
    # TODO: (2) convert backup files to server-file/directory format.

    def __init__(self):
        self.name = "cbtransfer"
        self.source_alias = "source"
        self.sink_alias = "destination"
        self.usage = \
            "%prog [options] source destination\n\n" \
            "Transfer couchbase cluster data from source to destination.\n\n" \
            "Examples:\n" \
            "  %prog http://HOST:8091 /backups/backup-20120512\n" \
            "  %prog /backups/backup-20120512 http://HOST:8091\n" \
            "  %prog /backups/backup-20120512 couchbase://HOST:8091\n" \
            "  %prog /backups/backup-20120512 memcached://HOST:11211\n" \
            "  %prog http://SOURCE:8091 http://DESTINATION:8091\n" \
            "  %prog /backups/backup-20120512 stdout: -t 1"

    def main(self, argv, opts_etc=None):
        if threading.current_thread().name == "MainThread":
            threading.current_thread().name = "mt"

        err, opts, source, sink = self.opt_parse(argv)
        if err:
            return err

        if opts_etc:
            opts.etc = opts_etc # Used for unit tests, etc.

        if source == sink:
            return "error: source and sink must be different;" \
                " source: " + source + \
                " sink: " + sink

        logging.info(self.name + "...")
        logging.info(" source : %s", source)
        logging.info(" sink   : %s", sink)
        logging.info(" opts   : %s", opts.safe)

        source_class, sink_class = self.find_handlers(opts, source, sink)
        if not source_class:
            return "error: unknown type of source: " + source
        if not sink_class:
            return "error: unknown type of sink: " + sink

        try:
            return pump.PumpingStation(opts, source_class, source,
                                       sink_class, sink).run()
        except KeyboardInterrupt:
            return "interrupted."

    def opt_parse(self, argv):
        opts, rest = self.opt_parser().parse_args(argv[1:])
        if len(rest) != 2:
            return "error: please provide both a %s and a %s" % \
                (self.source_alias, self.sink_alias), \
                None, None, None

        opts.extra = opt_parse_extra(opts.extra, self.opt_extra_defaults())
        opts.safe = opt_parse_helper(opts)

        return None, opts, rest[0], rest[1]

    def opt_parser(self):
        p = optparse.OptionParser(usage=self.usage,
                                  epilog=opt_extra_help(self.opt_extra_defaults()))
        self.opt_parser_options(p)

        # TODO: (1) parameter --username from env.
        # TODO: (1) parameter --password from env.

        return p

    def opt_parser_options(self, p):
        p.add_option("-b", "--bucket-source",
                     action="store", type="string", default=None,
                     help="""single bucket from source to transfer""")
        p.add_option("-B", "--bucket-destination",
                     action="store", type="string", default=None,
                     help="""when --bucket-source is specified, overrides the
                             destination bucket name; this allows you to transfer
                             to a different bucket; defaults to the same as the
                             bucket-source""")

        self.opt_parser_options_common(p)

    def opt_parser_options_common(self, p):
        p.add_option("-i", "--id",
                     action="store", type="int", default=None,
                     help="""allow only items that match a vbucketID""")
        p.add_option("-k", "--key",
                     action="store", type="string", default=None,
                     help="""allow only items with keys that match a regexp""")
        p.add_option("-n", "--dry-run",
                     action="store_true", default=False,
                     help="""no actual work; just validate parameters, files,
                             connectivity and configurations""")
        p.add_option("-u", "--username",
                     action="store", type="string", default=None,
                     help="REST username for cluster or server node")
        p.add_option("-p", "--password",
                     action="store", type="string", default=None,
                     help="REST password for cluster or server node")
        p.add_option("-t", "--threads",
                     action="store", type="int", default=4,
                     help="""number of concurrent workers""")
        p.add_option("-v", "--verbose",
                     action="count", default=0,
                     help="verbose logging; more -v's provide more verbosity")
        p.add_option("-x", "--extra",
                     action="store", type="string", default=None,
                     help="""extra, uncommon config parameters;
                             comma-separated key=val(,key=val)* pairs""")

    def opt_extra_defaults(self):
        return {
            "batch_max_size":  (1000,   "max # items per batch"),
            "batch_max_bytes": (400000, "max # of item value bytes per batch"),
            "report_dot":      (50,     "# batches before emitting a dot (.)"),
            "report_full":     (2000,   "# batches before emitting progress info"),
            "recv_min_bytes":  (4096,   "amount of bytes for every recv() call")
            }

    def find_handlers(self, opts, source, sink):
        return (PumpingStation.find_handler(opts, source, SOURCES),
                PumpingStation.find_handler(opts, sink, SINKS))


class Backup(Transfer):
    """Entry point for 2.0 cbbackup."""

    def __init__(self):
        self.name = "cbbackup"
        self.source_alias = "source"
        self.sink_alias = "backup_dir"
        self.usage = \
            "%prog [options] source backup_dir\n\n" \
            "Online/offline backup of a couchbase cluster or server node.\n\n" \
            "Examples:\n" \
            "  %prog http://HOST:8091 /backups/backup-20120512\n" \
            "  %prog couchbase://HOST:8091 /backups/backup-20120512"

    def opt_parser_options(self, p):
        p.add_option("-b", "--bucket-source",
                     action="store", type="string", default=None,
                     help="""single bucket from source to backup""")
        p.add_option("", "--single-node",
                     action="store_true", default=False,
                     help="""use a single server node from the source only,
                             not all server nodes from the entire cluster;
                             this single server node is defined by the source URL""")

        Transfer.opt_parser_options_common(self, p)

    def find_handlers(self, opts, source, sink):
        return PumpingStation.find_handler(opts, source, SOURCES), pump_bfd.BFDSink


class Restore(Transfer):
    """Entry point for 2.0 cbrestore."""

    # TODO: (1) Restore - opt_parse handle 1.8 backwards compatiable args.

    def __init__(self):
        self.name = "cbrestore"
        self.source_alias = "backup_dir"
        self.sink_alias = "destination"
        self.usage = \
            "%prog [options] backup_dir destination\n\n" \
            "Restores a single couchbase bucket.\n\n" \
            "Please first create the destination / bucket before restoring.\n\n" \
            "Examples:\n" \
            "  %prog /backups/backup-20120512 http://HOST:8091 \\\n" \
            "    --bucket=default\n" \
            "  %prog /backups/backup-20120512 couchbase://HOST:8091 \\\n" \
            "    --bucket=default\n" \
            "  %prog /backups/backup-20120512 memcached://HOST:11211 \\\n" \
            "    --bucket=sessions"

    def opt_parser_options(self, p):
        p.add_option("-a", "--add",
                     action="store_true", default=False,
                     help="""use add instead of set to not overwrite existing
                             items in the destination""")
        p.add_option("-b", "--bucket-source",
                     action="store", type="string", default=None,
                     help="""single bucket from the backup_dir to restore;
                             if the backup_dir only contains a single bucket,
                              then that bucket will be automatically used""")
        p.add_option("-B", "--bucket-destination",
                     action="store", type="string", default=None,
                     help="""when --bucket-source is specified, overrides the
                             destination bucket name; this allows you to restore
                             to a different bucket; defaults to the same as the
                             bucket-source""")

        Transfer.opt_parser_options_common(self, p)

        # TODO: (1) cbrestore parameter --create-design-docs=y|n
        # TODO: (1) cbrestore parameter -d DATA, --data=DATA
        # TODO: (1) cbrestore parameter --validate-only
        # TODO: (1) cbrestore parameter -H HOST, --host=HOST
        # TODO: (1) cbrestore parameter -p PORT, --port=PORT
        # TODO: (1) cbrestore parameter from non-active vbucket / MB-4583.
        # TODO: (1) cbrestore parameter option to override expiration?

    def find_handlers(self, opts, source, sink):
        return pump_bfd.BFDSource, PumpingStation.find_handler(opts, sink, SINKS)


# --------------------------------------------------

def opt_parse_helper(opts):
    logging_level = logging.WARN
    if opts.verbose >= 1:
        logging_level = logging.INFO
    if opts.verbose >= 2:
        logging_level = logging.DEBUG
    logging.basicConfig(format=pump.LOGGING_FORMAT, level=logging_level)

    opts_x = copy.deepcopy(opts)
    if opts_x.username:
        opts_x.username = "<xxx>"
    if opts_x.password:
        opts_x.password = "<xxx>"
    return opts_x

def opt_parse_extra(extra, extra_defaults):
    """Convert an extra string (comma-separated key=val pairs) into
       a dict, using default values from extra_defaults dict."""
    extra_in = dict([(x[0], x[1]) for x in
                     [(kv + '=').split('=') for kv in
                      (extra or "").split(',')]])
    for k, v in extra_in.iteritems():
        if k and not extra_defaults.get(k):
            sys.exit("error: unknown extra option: " + k)
    return dict([(k, int(extra_in.get(k, extra_defaults[k][0])))
                 for k in extra_defaults.iterkeys()])

def opt_extra_help(extra_defaults):
    return "Available extra config parameters (-x): " + \
        "; ".join(["%s=%s (%s)" %
                   (k, extra_defaults[k][0], extra_defaults[k][1])
                   for k in sorted(extra_defaults.iterkeys())])

# --------------------------------------------------

SOURCES = [pump_bfd.BFDSource,
           pump_mbf.MBFSource,
           pump_tap.TAPDumpSource]

SINKS = [pump_bfd.BFDSink,
         pump_mc.MCSink,
         pump_cb.CBSink,
         pump.StdOutSink]

# TODO: (1) - stdin source (saved memcached ascii protocol)
# TODO: (1) - stdout sink (saved memcached ascii protocol)
# TODO: (1) - _all_docs?include_docs=true source
# TODO: (1) - use QUIET commands
# TODO: (1) - verify that nth replica got the item
# TODO: (1) - ability to TAP a replica
# TODO: (10) - incremental backup/restore

if __name__ == '__main__':
    sys.exit(Transfer().main(sys.argv))

