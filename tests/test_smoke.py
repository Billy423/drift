def test_package_importable():
    import drift

    assert drift.__version__ == "1.0.0"
