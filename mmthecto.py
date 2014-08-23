"""
This file contains functions for targeting objects as part of the "distant
local group" project using MMT/Hectospec.
"""
import numpy as np

import targeting
from astropy import units as u

from astropy.coordinates import Angle
from astropy.table import Table


def make_catalog(host, fnout=None, targetfaintlim=(21.,22.), targetoutercutrad=300,
                 fluxrng=(17., 17.7), repeatflux=1, guidestarmagrng= (14, 15),
                 removegalsathighz=False, inclspecqsos=True, removespecstars=False,
                 fibermag_faintlimit=(22., 23.), fibermag_brightlimit=17,
                 fibermag_type='fibermag_r', useoutertargets=False):
    """
    Generates a catalog for the Hectospec "XFITFIBS" program.

    `targetfaintlim` can be just a mag to make everything "primary", or
    a 2-tuple that is (prisecboundary, faintestsec)

    `useoutertargets` means include targets beyond `targetoutercutrad`, but with
    a rank below where they would usually be

    Writes catalog to `fnout` if `fnout` is not None

    Returns the catalog as a `Table`

    `fibermag_faintlimit` is a lower limit *or* a two-tuple of limits for
    primary and secondary targets (secondary ignored if `targetfainlim` is just
    a single number) can also be None (`fibermag_brightlimit` can also be None)
    """
    fluxrank = 1
    hostrank = 2
    prisatrank = 3
    secsatrank = 5 if useoutertargets else 4 #the outer primaries get 4 if `useoutertargets` is True


    #add the host as the first entry
    rastr = Angle(host.ra, 'deg').format('hr', sep=':', precision=3)
    decstr = Angle(host.dec, 'deg').format('deg', sep=':', precision=3)
    mag = '{0:.2f}'.format(host.r + host.distmod)

    tabentries.append([rastr, decstr, host.name, str(hostrank), 'TARGET', mag])
    catlines.append('\t'.join(tabentries[-1]))

    try:
        prisecbounday, seclimit = targetfaintlim
    except TypeError:
        prisecbounday = seclimit = targetfaintlim


    if useoutertargets:
        targs = targeting.select_targets(host, faintlimit=seclimit, outercutrad=None, innercutrad=20, removegalsathighz=removegalsathighz, removespecstars=removespecstars, inclspecqsos=inclspecqsos)

        if targetoutercutrad < 0:  # arcmin
            outercutraddeg = -targetoutercutrad / 60.
        else:  # kpc
            outercutraddeg = np.degrees(targetoutercutrad / (1000 * host.distmpc))
        outermsk = targs['rhost'] > outercutraddeg #just used for counting purposes
    else:
        targs = targeting.select_targets(host, faintlimit=seclimit, outercutrad=targetoutercutrad, removegalsathighz=removegalsathighz, removespecstars=removespecstars, inclspecqsos=inclspecqsos)
        outermsk = None

    print 'Found', len(targs), 'targets'
    if outermsk is not None:
        print (~outermsk).sum(), 'are in the inner zone'
    if prisecbounday != seclimit:
        print np.sum(targs['r'] < prisecbounday), 'Primaries and',np.sum((targs['r'] > prisecbounday) & (targs['r'] < seclimit)),'Secondaries'
        if outermsk is not None:
            inrtargs = targs[~outermsk]
            print np.sum(inrtargs['r'] < prisecbounday), 'Primaries and',np.sum((inrtargs['r'] > prisecbounday) & (inrtargs['r'] < seclimit)),'Secondaries are in the inner zone'



    if fibermag_faintlimit is not None:
        try:
            prifiberlimit, secfiberlimit = fibermag_faintlimit
        except TypeError:
            prifiberlimit = secfiberlimit = fibermag_faintlimit

        fibmag = targs[fibermag_type]

        #filter things that have faint fiber mags in the primary targets
        primsk = (targs['r'] < prisecbounday) & (fibmag < prifiberlimit)
        #filter things that have faint fiber mags in the secondart targets
        secmsk = (targs['r'] > prisecbounday) & (targs['r'] < seclimit) & (fibmag < secfiberlimit)

        print 'Accepting', primsk.sum(), 'primary targets and', secmsk.sum(), 'secondaries according to fiber faint limit'
        targs = targs[primsk | secmsk]

    if fibermag_brightlimit is not None:
        msk = targs[fibermag_type] > fibermag_brightlimit
        targs = targs[msk]
        print 'Filtering', msk.sum(), ' targets due to fiber bright limit'

    targranks = []
    for t in targs:
        targranks.append(prisatrank if t['r'] < prisecbounday else secsatrank)
        if t['rhost'] > outercutraddeg:
            targranks[-1] += 1
    targranks = np.array(targranks)

    return generate_catalog(host, targs, targranks, fluxrng=fluxrng,
                            repeatflux=repeatflux, fluxrank=fluxrank,
                            guidestarmagrng=guidestarmagrng, fnout=fnout)

def colorcut_mask(targets, colorcut):
    """
    Returns a mask for the catalog objects `targets` that are True for objects
    matching the `colorcut` color ranges.
    """
    color_mask = np.ones(len(targets), dtype=bool)
    for colornm, (llim, ulim) in targeting.tighter_color_cuts.items():
        ulim = np.inf if ulim is None else ulim
        llim = (-np.inf) if llim is None else llim
        c1, c2 = colornm.split('-')
        c = targets[c1] - targets[c2]
        color_mask = color_mask & (llim < c) & (c < ulim)
    return color_mask


def generate_catalog(host, targs, targetranks, fnout=None, fluxfnout=None, fluxrank=1,
                     fluxrng=(17., 17.7), repeatflux=1, guidestarmagrng=(14, 15)):
    """
    given a host object `host`, an SDSS target catalog `targs`, and ranks for
    those targets `targetranks`, output the Hectospec catalog Table and (if
    `fnout` isn't None), save to disk in hectospec format.

    `fluxfnout` specifies a file to output the list of flux/calib stars to.
    """
    tabentries = []
    catlines = ['ra\tdec\tobject\trank\ttype\tmag',
                '--\t---\t------\t----\t----\t---']

    #entries for the actual targets
    print 'Including', len(targs), 'targets'
    for i, (t, rank) in enumerate(zip(targs, targetranks)):
        rastr = Angle(t['ra'], 'deg').format('hr', sep=':', precision=3)
        decstr = Angle(t['dec'], 'deg').format('deg', sep=':', precision=3)
        objnm = str(t['objID'])
        mag = '{0:.2f}'.format(t['r'])

        tabentries.append([rastr, decstr, objnm, str(rank), 'TARGET', mag])
        catlines.append('\t'.join(tabentries[-1]))

    #calibration stars
    fluxtargs = select_flux_stars(host.get_sdss_catalog(), fluxrng,
                                  fluxfnout=fluxfnout)
    print 'Found', len(fluxtargs), 'Flux stars'
    for t in fluxtargs:
        rastr = Angle(t['ra'], 'deg').format('hr', sep=':', precision=3)
        decstr = Angle(t['dec'], 'deg').format('deg', sep=':', precision=3)
        objnm = str(t['objID'])
        mag = '{0:.2f}'.format(t['r'])

        for i in range(repeatflux):
            tabentries.append([rastr, decstr, objnm + '_' + str(i + 1), str(fluxrank), 'TARGET', mag])
            catlines.append('\t'.join(tabentries[-1]))

    #add guide stars
    guidestars = select_guide_stars(host.get_sdss_catalog(), guidestarmagrng)
    print 'Found', len(guidestars), 'guide stars'
    for t in guidestars:
        rastr = Angle(t['ra'], 'deg').format('hr', sep=':', precision=3)
        decstr = Angle(t['dec'], 'deg').format('deg', sep=':', precision=3)
        objnm = str(t['objID'])
        mag = '{0:.2f}'.format(t['r'])

        tabentries.append([rastr, decstr, objnm, '', 'guide', mag])
        catlines.append('\t'.join(tabentries[-1]))

    catstr = '\n'.join(catlines)
    if fnout:
        with open(fnout, 'w') as f:
            f.write(catstr)

    tab = Table(names=catlines[0].split('\t'), dtype=['f', 'f', 'S50', 'S2', 'S20', 'f'])
    for e in tabentries:
        e[0] = Angle(e[0], unit=u.hour).degree
        e[1] = Angle(e[1], unit=u.degree).degree
        tab.add_row(e)
    return tab


def select_flux_stars(cat, magrng=(17, 17.7), extcorr=False, fluxfnout=None):
    """
    Identifies flux calibration stars suitable for Hectospec.

    Primarily selects ~F stars based on color cut.

    Uses the SDSS criteria for specphot standards:
    0.1 < (g-r) < 0.4

    their specphot stars are 16 < g < 17.1 and "reddening standards" are
    17.1 < g < 18.5
    """
    starmsk = cat['type'] == 6 #type==6 means star
    cat = cat[starmsk]


    u = cat['u']
    g = cat['g']
    r = cat['r']
    if extcorr:
        u = u - cat['Au']
        g = g - cat['Ag']
        r = r - cat['Ar']
    umg = u - g
    gmr = g - r

    #msk = (0.1 < gmr) & (gmr < 0.4) #old version

    std_color = (0.6 < umg) & (umg < 1.2)
    std_color = std_color & (0.0 < gmr) & (gmr < 0.6)
    std_color = std_color & (gmr > 0.75 * umg - 0.45)

    sp_std = std_color & (15.5 < g) & (g < 17.0)
    red_std = std_color & (17 < g) & (g < 18.5)

    minmag = min(*magrng)
    maxmag = max(*magrng)

    msk = (sp_std|red_std) & (minmag < r)& (r < maxmag)

    if fluxfnout:
        names = 'RA DEC u_psf g_psf r_psf i_psf z_psf extinction_r'.split()
        dat = [cat[msk]['ra'],
               cat[msk]['dec'],
               cat[msk]['psf_u'],
               cat[msk]['psf_g'],
               cat[msk]['psf_r'],
               cat[msk]['psf_i'],
               cat[msk]['psf_z'],
               cat[msk]['Ar']
              ]
        tab = Table(data=dat, names=names)
        with open(fluxfnout, 'w') as f:
            #f.write('#RA DEC u_psf g_psf r_psf i_psf z_psf extinction_r')
            tab.write(f, format='ascii.commented_header')

    return cat[msk]

def select_guide_stars(cat, magrng=(14, 15)):
    msk = cat['type'] == 6 #type==6 means star

    magrng = min(*magrng), max(*magrng)

    msk = msk & (magrng[0] < cat['r']) & (cat['r'] < magrng[1])

    return cat[msk]

def parse_cfg_file(fn):
    """
    returns coords, targets, ranks, fields
    """
    from astropy.coordinates import ICRSCoordinates
    from astropy.io import ascii

    intab = False
    inhdr = False

    coords = []
    targets = []
    ranks = []
    fields = []

    fi = 0
    with open(fn) as f:
        for l in f:
            if intab:
                if l.strip()=='':
                    intab = False
                else:
                    fiber, ra, dec, platex, platey, target, rank = [e.strip() for e in l.split('\t')]
                    coords.append(ICRSCoordinates(ra, dec, unit=('hr', 'deg')))
                    targets.append(int(float(target)))
                    ranks.append(int(float(rank)))
                    fields.append(fi)



            elif l.startswith('fiber'):
                hdrline = l
                inhdr = True
            elif inhdr and l.startswith('-'):
                hdrline2 = l
                inhdr = False
                intab = True
                fi += 1

    return np.array(coords), np.array(targets), np.array(ranks), np.array(fields)



