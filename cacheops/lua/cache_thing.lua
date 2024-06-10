local prefix = KEYS[1]
local key = KEYS[2]
local precall_key = KEYS[3]
local data = ARGV[1]
local dnfs = cjson.decode(ARGV[2])
local timeout = tonumber(ARGV[3])

if precall_key ~= prefix and redis.call('exists', precall_key) == 0 then
  -- Cached data was invalidated during the function call. The data is
  -- stale and should not be cached.
  return
end

-- Write data to cache
redis.call('setex', key, timeout, data)


-- A pair of funcs
-- NOTE: we depend here on keys order being stable
local conj_schema = function (conj)
    local parts = {}
    for field, _ in pairs(conj) do
        table.insert(parts, field)
    end

    return table.concat(parts, ',')
end

local conj_cache_key = function (db_table, conj)
    local parts = {}
    for field, val in pairs(conj) do
        table.insert(parts, field .. '=' .. tostring(val))
    end

    return prefix .. 'conj:' .. db_table .. ':' .. table.concat(parts, '&')
end


-- Update schemes and invalidators
for db_table, disj in pairs(dnfs) do
    for _, conj in ipairs(disj) do
        -- Ensure scheme is known
        redis.call('sadd', prefix .. 'schemes:' .. db_table, conj_schema(conj))

        -- Add new cache_key to list of dependencies
        local conj_key = conj_cache_key(db_table, conj)
        redis.call('sadd', conj_key, key)
        -- NOTE: an invalidator should live longer than any key it references.
        --       So we update its ttl on every key if needed.
        local conj_ttl = redis.call('ttl', conj_key)
        -- REDIS_7
        -- Removing `redis.call('expire', conj_key, timeout, 'gt')` as ttl is not setting up when its value is -1
        -- Refer - https://redis.io/docs/latest/commands/expire/
        if conj_ttl == -1 then
            -- Use NX when no expiry is present i.e. ttl = -1
            redis.call('expire', conj_key, timeout, 'nx')
        elseif conj_ttl < timeout then
            -- Use XX when current expiration time is less than timeout
            redis.call('expire', conj_key, timeout, 'xx')
        end
        -- /REDIS_7
        -- REDIS_4
        if conj_ttl < timeout then
            -- We set conj_key life with a margin over key life to call expire rarer
            -- And add few extra seconds to be extra safe
            redis.call('expire', conj_key, timeout * 2 + 10)
        end
        -- /REDIS_4
    end
end
