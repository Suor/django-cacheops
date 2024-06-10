local prefix = KEYS[1]
local key = KEYS[2]
local data = ARGV[1]
local schemes = cjson.decode(ARGV[2])
local conj_keys = cjson.decode(ARGV[3])
local timeout = tonumber(ARGV[4])
local rnd = ARGV[5]  -- A new value for empty stamps
local expected_checksum = ARGV[6]

-- Ensure schemes are known
for db_table, _schemes in pairs(schemes) do
    redis.call('sadd', prefix .. 'schemes:' .. db_table, unpack(_schemes))
end

-- Fill in invalidators and collect stamps
local stamps = {}
for _, conj_key in ipairs(conj_keys) do
    -- REDIS_7
    local stamp = redis.call('set', conj_key, rnd, 'nx', 'get') or rnd
    -- /REDIS_7
    -- REDIS_4
    local stamp = redis.call('get', conj_key)
    if not stamp then
        stamp = rnd
        redis.call('set', conj_key, rnd)
    end
    -- /REDIS_4
    table.insert(stamps, stamp)
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

-- Write data to cache along with a checksum of the stamps to see if any of them changed
local all_stamps = table.concat(stamps, ' ')
local stamp_checksum = redis.sha1hex(all_stamps)

if expected_checksum ~= '' and stamp_checksum ~= expected_checksum then
    -- Cached data was invalidated during the function call. The data is
    -- stale and should not be cached.
    return stamp_checksum -- This one is used for keep_fresh implementation
end

redis.call('set', key, stamp_checksum .. ':' .. data, 'ex', timeout)
