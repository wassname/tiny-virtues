"""Read a quoted story and its per-virtue labels. -- PI/Sol

uv run python examples/use_dataset.py [.local/public/data]
"""
import argparse
import json

from datasets import load_dataset

parser = argparse.ArgumentParser()
parser.add_argument('data_dir', nargs='?', default='hf://datasets/wassname/tiny-virtues/data')
data = parser.parse_args().data_dir.rstrip('/')
stories = load_dataset('parquet', data_files={'test':data+'/stories.parquet'}, split='test')
story = stories[0]
print(story['story'])
print(json.dumps({name:label['signed'] for name,label in story['virtue_labels'].items()}, indent=2))

virtues = load_dataset('parquet', data_files=data+'/virtues.parquet', split='train')
print(virtues[0]['virtue'], virtues[0]['definition'])
