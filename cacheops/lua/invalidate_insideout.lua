--!df flags=allow-undeclared-keys
local prefix = KEYS[1]
local db_table = ARGV[1]
local obj = cjson.decode(ARGV[2])

-- Format value for cache key (integers without .0)
local format_val = function (val)
    if type(val) == 'number' then
        if val == math.floor(val) then
            return string.format('%.0f', val)
        end
    end
    return tostring(val)
end

local conj_cache_key = function (db_table, scheme, obj)
    local parts = {}
    for field in string.gmatch(scheme, "[^,]+") do
        table.insert(parts, field .. '=' .. format_val(obj[field]))
    end

    return prefix .. 'conj:' .. db_table .. ':' .. table.concat(parts, '&')
end

-- Drop conj keys
local conj_keys = {}
local schemes = redis.call('smembers', prefix .. 'schemes:' .. db_table)
for _, scheme in ipairs(schemes) do
    redis.call('unlink', conj_cache_key(db_table, scheme, obj))
end
