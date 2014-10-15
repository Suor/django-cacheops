local key = KEYS[1]
local data = ARGV[1]
local dnfs = cjson.decode(ARGV[2])
local timeout = tonumber(ARGV[3])


-- Write data to cache
redis.call('setex', key, timeout, data)


-- A pair of funcs
local conj_schema = function (conj)
    local parts = {}
    for _, eq in ipairs(conj) do
        table.insert(parts, eq[1])
    end

    return table.concat(parts, ',')
end

local conj_cache_key = function (db_table, conj)
    local parts = {}
    for _, eq in ipairs(conj) do
        table.insert(parts, eq[1] .. '=' .. tostring(eq[2]))
    end

    return 'conj:' .. db_table .. ':' .. table.concat(parts, '&')
end


-- Update schemes and invalidators
for _, disj_pair in ipairs(dnfs) do
    local db_table = disj_pair[1]
    local disj = disj_pair[2]
    for _, conj in ipairs(disj) do
        -- Ensure scheme is known
        redis.call('sadd', 'schemes:' .. db_table, conj_schema(conj))

        -- Add new cache_key to list of dependencies
        local conj_key = conj_cache_key(db_table, conj)
        redis.call('sadd', conj_key, key)
        -- NOTE: an invalidator should live longer than any key it references.
        --       So we update its ttl on every key if needed.
        -- NOTE: if CACHEOPS_LRU is True when invalidators should be left persistent,
        --       so we strip next section from this script.
        -- TOSTRIP
        local conj_ttl = redis.call('ttl', conj_key)
        if conj_ttl < timeout then
            -- We set conj_key life with a margin over key life to call expire rarer
            -- And add few extra seconds to be extra safe
            redis.call('expire', conj_key, timeout * 2 + 10)
        end
        -- /TOSTRIP
    end
end
