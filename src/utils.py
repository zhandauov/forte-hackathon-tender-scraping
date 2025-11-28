import os
import shutil

def clear_folder(path: str):
    if not os.path.isdir(path):
        raise ValueError(f"{path} is not a directory")

    for name in os.listdir(path):
        full_path = os.path.join(path, name)

        if os.path.isfile(full_path) or os.path.islink(full_path):
            os.remove(full_path)
        elif os.path.isdir(full_path):
            shutil.rmtree(full_path)