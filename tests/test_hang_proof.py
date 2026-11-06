"""Throwaway: the real config against a test that never ends."""
import socket


def test_stuck_forever() -> None:
    left, _right = socket.socketpair()
    left.recv(1)


def test_the_next_test_still_runs() -> None:
    assert True
