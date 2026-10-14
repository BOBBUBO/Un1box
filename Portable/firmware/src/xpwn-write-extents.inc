/* XPwn's overflow keys are logical allocation-block offsets, and extent
 * records on disk are big endian. Preserve both when a catalog grows past
 * the eight inline extents during a data-volume transfer. */
int writeExtents(RawFile* rawFile) {
    if(!removeExtents(rawFile)) return FALSE;
    HFSPlusForkData *fork=rawFile->forkData;
    Extent *extent=rawFile->extents;
    uint32_t logical=0;
    memset(fork->extents,0,sizeof(HFSPlusExtentRecord));
    for(int i=0;i<8 && extent;i++,extent=extent->next) {
        fork->extents[i].startBlock=extent->startBlock;
        fork->extents[i].blockCount=extent->blockCount;
        logical+=extent->blockCount;
    }
    while(extent) {
        HFSPlusExtentKey key;
        memset(&key,0,sizeof(key));
        key.keyLength=sizeof(key)-sizeof(key.keyLength);
        key.fileID=rawFile->id;
        key.startBlock=logical;
        HFSPlusExtentRecord record;
        memset(record,0,sizeof(record));
        for(int i=0;i<8 && extent;i++,extent=extent->next) {
            record[i].startBlock=extent->startBlock;
            record[i].blockCount=extent->blockCount;
            logical+=extent->blockCount;
        }
        flipExtentRecord(&record);
        if(!addToBTree(rawFile->volume->extentsTree,(BTKey*)&key,sizeof(record),(unsigned char*)record))return FALSE;
    }
    return TRUE;
}
