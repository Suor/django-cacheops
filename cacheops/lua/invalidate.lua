local model = ARGV[1]
local obj = cjson.decode(ARGV[2])

local schemes = redis.call('smembers', 'schemes:' .. model)

-- conj cache key
local conj_cache_key = function (model, scheme, obj)
    local parts = {}
    for field in string.gmatch(scheme, "[^,]+") do
        table.insert(parts, field .. '=' .. tostring(obj[field]))
    end

    return 'conj:' .. model .. ':' .. table.concat(parts, '&')
end

-- calculate conj keys
local conj_keys = {}
for _, scheme in ipairs(schemes) do
    table.insert(conj_keys, conj_cache_key(model, scheme, obj))
end

-- get cache keys
redis.call('set', '-- cache_keys', cjson.encode(conj_keys))
if next(conj_keys) ~= nil then
    local cache_keys = redis.call('sunion', unpack(conj_keys))
    -- we delete cache keys since they are invalid
    -- and conj keys as they will refer only deleted keys
    redis.call('del', unpack(conj_keys))
    if next(cache_keys) ~= nil then
        redis.call('del', unpack(cache_keys))
    end
end
