### 1.0.1
- Removed the md5 hash approach as the hashtag prefix. This approach hurts the invalidation update badly. Also we chose the md5 approach since we didn’t know how to generate invalidator key on update/delete query. But that problem is already solved, we know how to generator the invalidator key correctly therefore md5 approach is no longer suitable
- Moved the lua/cache_thing.lua script to python to use the cluster feature
- Update cluster/invalidation.py to improve invalidation speed by replace the old approach with newer one
- No longer need prefix to distribute the redis key. It should be evenly distributed now
