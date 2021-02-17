local prefix = KEYS[1]
local db_table = ARGV[1]
local obj = cjson.decode(ARGV[2])

-- Utility functions
local conj_cache_key = function (db_table, scheme, obj)
    local parts = {}
    for field in string.gmatch(scheme, "[^,]+") do
        table.insert(parts, field .. '=' .. tostring(obj[field]))
    end

    return prefix .. 'conj:' .. db_table .. ':' .. table.concat(parts, '&')
end

local call_in_chunks = function (command, args)
    local step = 1000
    for i = 1, #args, step do
        redis.call(command, unpack(args, i, math.min(i + step - 1, #args)))
    end
end


-- Calculate conj keys
local conj_keys = {}
local schemes = redis.call('smembers', prefix .. 'schemes:' .. db_table)
for _, scheme in ipairs(schemes) do
    table.insert(conj_keys, conj_cache_key(db_table, scheme, obj))
end


-- Delete cache keys and refering conj keys
if next(conj_keys) ~= nil then
    local cache_keys = redis.call('sunion', unpack(conj_keys))
    -- we delete cache keys since they are invalid
    -- and conj keys as they will refer only deleted keys
    redis.call("unlink", unpack(conj_keys))
    if next(cache_keys) ~= nil then
        -- NOTE: can't just do redis.call('del', unpack(...)) cause there is limit on number
        --       of return values in lua.
        call_in_chunks('del', cache_keys)
    end
end
