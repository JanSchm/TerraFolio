"""Process-pool executor and event pump (issue 3A).

Deliberately re-exports **nothing**. ``runner.worker`` pins every numeric
thread pool the moment it is imported, which is exactly right in a worker
process and a surprise anywhere else — so importing the package must not drag
that in. Callers name the module they want.
"""
