import collections
import resolve
import parsepy
import os
import networkx as nx

class File(object):
    """A file in the file system. E.g. "/foo/bar/baz.py".

    In the presence of symlinks and hard links, a file may exist in multiple places.
    We do not model that, and instead treat files with different (absolute) paths
    as different.
    """
    __slots__ = [
            "path",  # absolute path, e.g. "/foo/bar/baz.py"
            "modules",  # modules implemented by this file
            "deps",  # files this file depends on
            "rdeps",  # files that depend on us
    ]

    def __init__(self, path):
        self.path = path
        self.modules = []
        self.deps = []
        self.rdeps = []


class Module(object):
    """A Python module. E.g. "foo.bar.baz".

    We treat modules and packages the same. In other words, a file like
    "foo/bar/__init__.py" might represent the module "foo.bar".
    """
    __slots__ = [
            "name",  # name of the module, e.g. "foo.bar.baz"
            "file",  # file defining this module.
    ]


class FileCollection(object):
    """A list of files."""

    def __init__(self, files=()):
        self.files = {f.path: f for f in files}

    def add_file(self, f):
        self.files[f.path] = f

    def __iter__(self):
        return iter(self.files.values())


class Cycle(object):
    def __init__(self, edges, root=''):
        self.root = root
        self.edges = edges
        self.nodes = [x[0] for x in self.edges]

    def _fmt(self, node):
        if isinstance(node, Cycle):
            return node.pp()
        else:
            return os.path.relpath(node, self.root)

    def flatten_nodes(self):
        out = []
        for n in self.nodes:
            if isinstance(n, Cycle):
                out.extend(n.flatten_nodes())
            else:
                out.append(n)
        return out

    def __contains__(self, v):
        return v in self.nodes

    def pp(self):
        return "[" + '->'.join([self._fmt(f) for f in self.nodes]) + "]"

    def __repr__(self):
        return "Cycle(" + str(sorted(self.nodes)) + ")"

    def __str__(self):
        return self.pp()


class ImportGraph(object):
    def __init__(self, path, typeshed_location):
        self.path = path
        self.typeshed_location = typeshed_location
        self.broken_deps = collections.defaultdict(set)
        self.graph = nx.DiGraph()
        self.root = None

    def get_file_deps(self, filename):
        r = resolve.Resolver(self.path, filename)
        resolved = []
        unresolved = []
        for imp in parsepy.scan_file(filename):
            try:
                f = r.resolve_import(imp)
                if not f.endswith(".so"):
                    resolved.append(os.path.abspath(f))
            except resolve.ImportException:
                unresolved.append(imp)
        return (resolved, unresolved)

    def add_file(self, filename):
        resolved, unresolved = self.get_file_deps(filename)
        self.graph.add_node(filename)
        for f in resolved:
            self.graph.add_node(f)
            self.graph.add_edge(filename, f)
        for imp in unresolved:
            self.broken_deps[filename].add(imp)

    def add_file_recursive(self, filename):
        queue = collections.deque([filename])
        seen = set()
        while queue:
            filename = queue.popleft()
            self.graph.add_node(filename)
            deps, broken = self.get_file_deps(filename)
            for f in broken:
                self.broken_deps[filename].add(f)
            for f in deps:
                if (not f in self.graph.nodes and
                    not f in seen and
                    f.endswith(".py")):
                    queue.append(f)
                    seen.add(f)
                self.graph.add_node(f)
                self.graph.add_edge(filename, f)

    def find_root(self, recalculate=False):
        if recalculate or not self.root:
            keys = set(x[0] for x in self.graph.edges)
            prefix = os.path.commonprefix(list(keys))
            if not os.path.isdir(prefix):
                prefix = os.path.dirname(prefix)
            self.root = prefix
        return self.root

    def extract_cycle(self, cycle):
        self.graph.add_node(cycle)
        edges = list(self.graph.edges)
        for k, v in edges:
            if k not in cycle and v in cycle:
                self.graph.remove_edge(k, v)
                self.graph.add_edge(k, cycle)
            elif k in cycle and v not in cycle:
                self.graph.remove_edge(k, v)
                self.graph.add_edge(cycle, v)
        for node in cycle.nodes:
            self.graph.remove_node(node)

    def format(self, node):
        prefix = self.find_root()
        if isinstance(node, Cycle):
            return node.pp()
        elif node.startswith(self.typeshed_location):
            return "[%s]" % os.path.relpath(node, self.typeshed_location)
        else:
            return os.path.relpath(node, prefix)

    def inspect_graph(self):
        prefix = self.find_root()
        keys = set(x[0] for x in self.graph.edges)
        for key in sorted(keys):
            k = self.format(key)
            for _, value in sorted(self.graph.edges([key])):
                v = self.format(value)
                print("  %s -> %s" % (k, v))
            for value in sorted(self.broken_deps[key]):
                print("  %s -> <%s>" % (k, value))

    def collapse_cycles(self):
        prefix = self.find_root()
        while True:
            try:
                cycle = Cycle(nx.find_cycle(self.graph), prefix)
                self.extract_cycle(cycle)
            except nx.NetworkXNoCycle:
                break

    def deps_list(self):
        out = []
        for node in nx.topological_sort(self.graph):
            if isinstance(node, Cycle):
                out.append(node.flatten_nodes())
            elif node.endswith(".py"):
                # add a one-element list for uniformity
                out.append([node])
            else:
                # We don't care about pyi deps
                pass
        return reversed(out)

    def _print_tree(self, root, seen, indent=0):
        if root in seen:
            return
        if not isinstance(root, Cycle) and root.endswith(".pyi"):
            return
        seen.add(root)
        print(" "*indent + self.format(root))
        for _, v in self.graph.edges([root]):
            self._print_tree(v, seen, indent=indent+2)

    def print_tree(self):
        root = nx.topological_sort(self.graph).next()
        seen = set()
        self._print_tree(root, seen)

    def print_topological_sort(self):
        for node in nx.topological_sort(self.graph):
            if isinstance(node, Cycle) or node.endswith(".py"):
                print(self.format(node))
